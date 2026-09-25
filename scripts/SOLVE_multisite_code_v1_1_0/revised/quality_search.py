"""Bounded search-route checks and metadata-only diagnostics; no search snippets."""
import json
import time


CONTROLS = [
    {'query': 'Bergwitzsee', 'terms': ['Bergwitzsee']},
    {'query': '"Arendsee" site:ufz.de', 'terms': ['Arendsee'], 'domain': 'ufz.de'},
]


def initialize(r):
    r.store.db.executescript('''
    CREATE TABLE IF NOT EXISTS search_route_health(
      route TEXT PRIMARY KEY, status TEXT, checked REAL, next_try REAL, detail TEXT);
    CREATE TABLE IF NOT EXISTS search_options(search_id TEXT PRIMARY KEY, page INTEGER);
    CREATE TABLE IF NOT EXISTS preflight_scores(task_id INTEGER PRIMARY KEY, review_id TEXT,
      importance REAL, tier TEXT);
    ''')
    r.health_used = 0
    r.search_http_used = 0
    r.exploration_used = {}


def route_key(r, engine, core):
    return core.stable([r.cfg['search']['base_url'], engine, CONTROLS, 'quality-1'])


def mark(r, engine, ok, detail, core):
    now = time.time()
    r.store.db.execute('INSERT OR REPLACE INTO search_route_health VALUES(?,?,?,?,?)',
        (route_key(r, engine, core), 'healthy' if ok else 'unhealthy', now,
         now + (r.cfg['retrieval']['health_ttl_seconds'] if ok else r.cfg['retrieval']['cooldown_seconds']),
         json.dumps(dict(engine=engine, **detail), ensure_ascii=False)))
    r.store.event('search_route_health', dict(engine=engine, healthy=ok, **detail))
    r.store.db.commit()


def domain_match(url, domain, core):
    host = core.host_of(url)
    return host == domain or host.endswith('.' + domain)


def healthy(r, engine, core):
    key = route_key(r, engine, core)
    row = r.store.one('SELECT * FROM search_route_health WHERE route=?', (key,))
    if row and row['next_try'] > time.time():
        return row['status'] == 'healthy'
    if r.health_used + len(CONTROLS) > r.cfg['retrieval']['max_health_calls_per_run']:
        return False
    for control in CONTROLS:
        r.health_used += 1
        try:
            hits, warnings = core.searxng_search(r.cfg, control['query'], engine=engine)
            matches = [h for h in hits if core.match_terms(h['title'] + ' ' + core.up.unquote(h['url']), control['terms'])
                       and (not control.get('domain') or domain_match(h['url'], control['domain'], core))]
            if not matches:
                mark(r, engine, False, dict(control=control['query'], reason='control_no_matching_hit',
                     sample=hits[:3], warnings=warnings), core)
                return False
        except (core.FetchProblem, ValueError, KeyError) as exc:
            mark(r, engine, False, dict(control=control['query'], reason=str(exc)[:200]), core)
            return False
    mark(r, engine, True, dict(reason='controls_passed'), core)
    return True


def available(r, core):
    return [e for e in r.cfg['retrieval']['engines'] if healthy(r, e, core)]


def crossref_next_try(r):
    return metadata_next_try(r, 'crossref')


def metadata_next_try(r, provider):
    # Provider backoff also applies to other lakes and new query variants.
    row = r.store.one("SELECT MAX(next_try) due FROM searches WHERE provider=? AND status='retry'", (provider,))
    return (row or {}).get('due') or 0


def routes_unavailable(r, core):
    if r.cfg['search']['provider'] != 'searxng' or not r.cfg['retrieval']['enabled']:
        return False
    rows = [r.store.one('SELECT * FROM search_route_health WHERE route=?', (route_key(r, e, core),))
            for e in r.cfg['retrieval']['engines']]
    return bool(rows) and all(row and row['status'] == 'unhealthy' for row in rows)


def next_search_wakeup(r, core):
    """Read-only earliest useful retry; completed searches require no wait."""
    if not r.cfg['retrieval']['wait_for_search_routes'] or r.cfg['search']['strategy'] != 'focused':
        return None
    if max(r.search_used, r.search_http_used) >= r.cfg['limits']['max_searches_per_run']:
        return None
    pending = [s for s in r.store.rows("SELECT * FROM searches WHERE status IN ('pending','retry')")
               if s['profile'] in r.active]
    now, due = time.time(), []
    import scientific_discovery
    for provider, enabled in [('crossref', r.cfg['retrieval']['crossref_enabled']),
                              ('serper', r.cfg['search']['provider'] == 'serper'),
                              ('datacite', scientific_discovery.enabled(r.cfg))]:
        if enabled and any(s['provider'] == provider for s in pending):
            retry_at = metadata_next_try(r, provider)
            if retry_at > now:
                due.append(retry_at)
    web = [s for s in pending if s['provider'] == 'searxng']
    if web and r.cfg['search']['provider'] == 'searxng':
        query_due = min(s['next_try'] for s in web)
        can_check = r.health_used + len(CONTROLS) <= r.cfg['retrieval']['max_health_calls_per_run']
        for engine in r.cfg['retrieval']['engines']:
            row = r.store.one('SELECT * FROM search_route_health WHERE route=?', (route_key(r, engine, core),))
            if row and row['status'] == 'healthy' and row['next_try'] > now:
                if query_due > now:
                    due.append(query_due)
            elif row and can_check:
                ready = max(query_due, row['next_try'])
                if ready > now:
                    due.append(ready)
    return min(due) if due else None


def plausible(hits, wb, query, core):
    """Batch anomaly signal, not an exclusion rule for individual opaque documents."""
    if not hits:
        return True
    site = core.re.search(r'(?<!\S)site:([\w.-]+)', query)
    if site and not any(domain_match(h['url'], site[1], core) for h in hits):
        return False
    for h in hits:
        text = h.get('title', '') + ' ' + core.up.unquote(h['url'])
        if core.identity_matches(wb, text):
            return True
        if any(domain_match(h['url'], d, core) for d in wb.get('preferred_domains', [])):
            return True
        if any(w in core.norm(text) for w in core.DOCUMENT_WORDS):
            return True
    return False


def search(r, task, core):
    page = (r.store.one('SELECT page FROM search_options WHERE search_id=?', (task['id'],)) or {}).get('page', 1)
    for engine in available(r, core):
        # Every network attempt (including fallbacks/pages) consumes the shared search budget.
        if r.search_http_used >= r.cfg['limits']['max_searches_per_run']:
            raise core.FetchProblem('search_call_budget', retry=True)
        r.search_http_used += 1
        try:
            hits, warnings = core.searxng_search(r.cfg, task['query'], engine=engine, page=page)
            if warnings:
                r.store.event('search_engine_failures', dict(search_id=task['id'], engines=warnings, partial=bool(hits)))
            if (not hits and warnings) or not plausible(hits, r.bodies[task['profile']], task['query'], core):
                mark(r, engine, False, dict(reason='search_response_anomaly', query=task['query'], sample=hits[:3], warnings=warnings), core)
                r.store.event('search_quarantined', dict(search_id=task['id'], engine=engine, results=hits, warnings=warnings))
                continue
            r.store.event('search_route_used', dict(search_id=task['id'], engine=engine, page=page, warnings=warnings))
            return hits
        except (core.FetchProblem, ValueError, KeyError) as exc:
            mark(r, engine, False, dict(reason=str(exc)[:200]), core)
    raise core.FetchProblem('search_no_healthy_route', retry=True, delay=r.cfg['retrieval']['cooldown_seconds'])


def crossref_search(r, task, core):
    """Crossref provides bibliographic candidates, not guaranteed full texts."""
    if r.search_http_used >= r.cfg['limits']['max_searches_per_run']:
        raise core.FetchProblem('search_call_budget', retry=True)
    retry_at = crossref_next_try(r)
    if retry_at > time.time():
        raise core.FetchProblem('crossref_cooldown', retry=True, delay=retry_at-time.time())
    time.sleep(r.cfg['search']['delay_seconds'])
    r.search_http_used += 1
    params = core.up.urlencode({'query.bibliographic': task['query'], 'rows': r.cfg['search']['count']})
    req = core.ur.Request('https://api.crossref.org/works?' + params,
                         headers={'Accept': 'application/json', 'User-Agent': r.cfg['user_agent']})
    try:
        with core.ur.build_opener(core.NoRedirect()).open(req, timeout=r.cfg['search']['timeout_seconds']) as res:
            raw = res.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('crossref_response_too_large')
        rows = json.loads(raw)['message']['items']
        if not isinstance(rows, list):
            raise ValueError('crossref_invalid_items')
        hits = []
        for row in rows:
            title = ' '.join(row.get('title') or [])
            # Citations may omit the lake name. Generic lake searches must name the lake.
            if task['family'] != 'citation' and not core.identity_matches(r.bodies[task['profile']], title):
                continue
            doi = row.get('DOI', '')
            url = core.canonical('https://doi.org/' + doi) if doi else None
            if url:
                hits.append(dict(url=url, title=title, doi=doi, engines=['crossref'], retrieved_by='crossref', retrieved_at=core.utc()))
        return hits
    except core.urllib.error.HTTPError as exc:
        raise core.FetchProblem('crossref_http_' + str(exc.code), retry=True, delay=core.retry_after(exc.headers.get('Retry-After'))) from None
    except (OSError, core.urllib.error.URLError):
        raise core.FetchProblem('crossref_network_error', retry=True) from None

"""Fair, evidence-driven search stages; no learning claim or implicit URL-count reward."""
import json
import time
import scientific_discovery


def plan(r):
    for wb in r.cfg['waterbodies']:
        profile = r.profiles[wb['id']]
        for url in wb['seed_urls']:
            if not r.store.one('SELECT id FROM tasks WHERE profile=? AND url=?',(profile,url)):
                r.enqueue(profile, url, label=wb.get('seed_titles', {}).get(url, 'Geprüfter Rechercheeinstieg'), priority=94, force=True)
        # Additional entry points are operational discovery settings: keep them
        # outside the identity profile so existing reviews/tasks remain active.
        for source in r.cfg['scientific_sources']:
            if source['waterbody_id'] != wb['id']:
                continue
            url = source['url']
            existing = r.store.one('SELECT status,reason FROM tasks WHERE profile=? AND url=?', (profile,url))
            if not existing or existing['status'] == 'deferred' and existing['reason'] in {'low_priority','depth_budget'}:
                r.enqueue(profile, url, label=source['title'], priority=94, force=True,
                          branch='scientific_source:' + url)
        r.add_search(profile, '"' + wb.get('query_name', wb['name']).replace('"', '') + '"',
                     'overview', 'Gewässer ohne zusätzliche Einschränkungen', 95)
        if r.cfg['start_links']['enabled']:
            for term in r.cfg['start_links']['initial_keywords']:
                r.add_search(profile,wb.get('query_name',wb['name'])+' '+term.strip(),
                    'start_pool_initial','Zusätzlicher Suchbegriff für die erste Startlink-Auswahl',90)
        if r.cfg['retrieval']['crossref_enabled']:
            r.add_search(profile, wb.get('query_name', wb['name']), 'science_metadata',
                         'Bibliografische Suche bei Crossref', 80, provider='crossref')
        if scientific_discovery.enabled(r.cfg):
            name = scientific_discovery.names(r.cfg, wb)[0]
            r.add_search(profile, name, 'dataset_metadata',
                         'Datensätze und Repositorien über DataCite', 82, provider='datacite')
    r.store.db.commit()


def feedback(r, profile):
    result, seen, covered = {}, set(), set()
    for t in r.store.rows('''SELECT t.branch,t.status,t.reason,u.sha,v.status review_status,v.response
          FROM tasks t LEFT JOIN urls u ON u.url=t.url LEFT JOIN reviews v ON v.id=t.review_id
          WHERE t.profile=? ORDER BY v.created,t.id''', (profile,)):
        stat = result.setdefault(t['branch'], dict(checked=0, gain=0))
        if t['status'] == 'done' or t['reason'] == 'llm_preflight_deferred':
            stat['checked'] += 1
        if not t['response'] or t['review_status'] != 'complete':
            continue
        d = json.loads(t['response'])
        if d['decision'] != 'relevant' or d.get('identity_status') != 'candidate' or not d.get('topic_hits'):
            continue
        if t['sha'] and t['sha'] not in seen:
            seen.add(t['sha'])
            new = set(d['topic_hits']) - covered
            covered.update(new)
            stat['gain'] += 1 + len(new)
    return result, covered


def options(r, wb, covered):
    name = '"' + wb.get('query_name', wb['name']).replace('"', '') + '"'
    domains=list(dict.fromkeys(wb.get('preferred_domains', [])[:3] +
                 [source['search_domain'] for source in r.cfg['scientific_sources']
                  if source['waterbody_id'] == wb['id'] and source.get('search_domain')]))
    if r.cfg['scientific_discovery']['enabled']:
        domains = list(dict.fromkeys(domains + r.cfg['scientific_discovery']['domains']))
    for domain in domains[:1]:
        yield f'{name} site:{domain}', 'authority'
    yield f'{name} filetype:pdf', 'documents'
    for alias in (wb.get('aliases', []) + wb.get('identifiers', []))[:2]:
        yield '"' + alias.replace('"', '') + '"', 'alias'
    for term in wb.get('english_terms', ['limnology', 'sediment']):
        yield f'{name} {term}', 'science'
    for domain in domains[1:]:
        yield f'{name} site:{domain}', 'authority'
    for topic in r.cfg['topics']:
        if topic['name'] not in covered:
            yield f'{name} {(topic.get("keywords") or [topic["name"]])[0]}', 'topic'


def science_options(r, wb, covered, history, core):
    """Bibliographic terms work independently of web-search engines/operators."""
    for previous in history:
        query = previous['query']
        if previous['family'] == 'review' and not core.re.search(r'\b(?:site|filetype|inurl):', query):
            yield query, 'science_review'
    for alias in wb.get('aliases', [])[:2]:
        yield alias, 'science_alias'
    name = wb.get('query_name', wb['name'])
    terms = list(dict.fromkeys(wb.get('english_terms', []) +
                 ['phosphorus', 'groundwater', 'sediment', 'restoration', 'eutrophication', 'paleolimnology']))
    for term in terms:
        yield f'{name} {term}', 'science_topic'
    for topic in r.cfg['topics']:
        if topic['name'] not in covered:
            for term in topic.get('keywords', [])[:2]:
                yield f'{name} {term}', 'science_topic'


def batch_waiting(r, search):
    attempted = getattr(r, 'attempted_tasks', set())
    for task in r.store.rows("SELECT id,profile,reason FROM tasks WHERE branch=? AND status IN ('pending','retry','in_progress')", (search['id'],)):
        if task['id'] in attempted:
            continue
        if task['reason'] == 'preflight_exploration_budget' and r.exploration_used.get(task['profile'], 0) >= r.cfg['preflight']['exploration_per_waterbody']:
            continue
        return True
    return False


def next_search(r, core):
    import quality_search
    if r.cfg['search']['provider']=='none':
        return None
    routes = quality_search.available(r, core) if r.cfg['search']['provider'] == 'searxng' else ['configured']
    providers = [r.cfg['search']['provider']] if routes else []
    if r.cfg['start_links']['enabled'] and 'serper' in providers:
        providers.remove('serper')  # Dedicated entry-pool phase owns these queries.
    if 'serper' in providers and quality_search.metadata_next_try(r, 'serper') > time.time():
        providers.remove('serper')
    if r.cfg['retrieval']['crossref_enabled'] and quality_search.crossref_next_try(r) <= time.time():
        providers.append('crossref')
    if scientific_discovery.enabled(r.cfg) and quality_search.metadata_next_try(r, 'datacite') <= time.time():
        providers.append('datacite')
    histories = {p:r.store.rows('SELECT * FROM searches WHERE profile=?', (p,)) for p in r.active}
    # Count actual attempts/completions, not the number of preplanned rows.
    order = sorted(r.active, key=lambda p:(sum(q['status'] not in {'pending','disabled','deferred_budget'} for q in histories[p]), r.bodies[p]['name']))
    for profile in order:
        history = histories[profile]
        pending = [q for q in history if q['status'] in {'pending','retry'} and q['next_try'] <= time.time()
                   and q['provider'] in providers]
        if pending:
            return max(pending, key=lambda q:q['priority'])
        # Queues on a failed provider must not consume a working provider's
        # planning allowance. The separate global query cap still applies.
        if len([q for q in history if q['provider'] in providers]) >= r.cfg['search']['adaptive_max_queries']:
            continue
        stats, covered = feedback(r, profile)
        for provider in providers:
            provider_history = [q for q in history if q['provider'] == provider]
            informative = [q for q in provider_history if q['status'] in {'done','no_results'}]
            recent = sorted(informative, key=lambda q:q['completed'] or '')
            plateau = r.cfg['search']['plateau_queries']
            if len(recent) >= max(4, plateau) and all(stats.get(q['id'], {}).get('gain', 0) == 0 for q in recent[-plateau:]):
                if all(q['status'] == 'no_results' or stats.get(q['id'], {}).get('checked', 0) >= min(3, q['result_count'] or 1) for q in recent[-plateau:]):
                    continue
            waiting = any(q['status'] == 'done' and stats.get(q['id'],{}).get('checked',0) < min(3,q['result_count'] or 1)
                          and batch_waiting(r, q) for q in recent)
            if waiting:
                continue
            for q in recent:
                page = (r.store.one('SELECT page FROM search_options WHERE search_id=?',(q['id'],)) or {}).get('page',1)
                if provider in {'searxng','serper'} and stats.get(q['id'],{}).get('gain',0)>0 and page < r.cfg['retrieval']['max_pages']:
                    sid = r.add_search(profile,q['query'],'next_page','Zusätzliche Seite nach bestätigtem Quellengewinn',85,page=page+1,provider=provider)
                    candidate = r.store.one('SELECT * FROM searches WHERE id=?',(sid,))
                    if candidate and candidate['status']=='pending':
                        return candidate
            used = {q['query'].casefold() for q in provider_history}
            if provider == 'datacite':
                variants = scientific_discovery.options(r.cfg, r.bodies[profile])
            elif provider == 'crossref':
                variants = science_options(r,r.bodies[profile],covered,history,core)
            else:
                variants = options(r,r.bodies[profile],covered)
            for query,family in variants:
                if query.casefold() not in used:
                    sid=r.add_search(profile,query,family,'Neue Suchrichtung nach Prüfung des Quellengewinns',85,provider=provider)
                    r.store.db.commit()
                    candidate = r.store.one('SELECT * FROM searches WHERE id=?',(sid,))
                    if candidate and candidate['status'] == 'pending':
                        return candidate
    return None

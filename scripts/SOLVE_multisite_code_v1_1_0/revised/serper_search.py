"""Serper organic search; credentials stay in the header, snippets stay transient."""
import json
import os
import re
import time

ENDPOINT = 'https://google.serper.dev/search'


def normalize_query(query, simple=False):
    if not simple:
        return query
    # The current account rejects advanced query patterns. Keep the search
    # terms (including domain names) without asserting an exact/site filter.
    query = query.replace('"', '').replace('“', '').replace('”', '')
    query = re.sub(r'\b(?:site|filetype|inurl|intitle|ext):', '', query, flags=re.I)
    return ' '.join(query.split())


def search(r, task, core):
    import quality_search
    c = r.cfg['search']
    key = os.getenv(c['api_key_env'], '')
    if not key:
        raise ValueError('Such-API-Schlüssel fehlt: '+c['api_key_env'])
    if r.search_http_used >= r.cfg['limits']['max_searches_per_run']:
        raise core.FetchProblem('search_call_budget', retry=True)
    due = quality_search.metadata_next_try(r, 'serper')
    if due > time.time():
        raise core.FetchProblem('serper_cooldown', retry=True, delay=due-time.time())
    page = (r.store.one('SELECT page FROM search_options WHERE search_id=?', (task['id'],)) or {}).get('page', 1)
    payload = dict(q=task['query'], num=c['count'], page=page,
                   gl=c['country'].lower(), hl=c['language'])
    req = core.ur.Request(ENDPOINT, data=json.dumps(payload).encode('utf-8'),
        headers={'X-API-KEY':key, 'Content-Type':'application/json', 'Accept':'application/json'})
    time.sleep(c['delay_seconds'])
    r.search_http_used += 1
    try:
        # NoRedirect prevents forwarding the key to an unexpected host.
        with core.ur.build_opener(core.NoRedirect()).open(req, timeout=c['timeout_seconds']) as response:
            raw = response.read(4*1024*1024+1)
        if len(raw) > 4*1024*1024:
            raise ValueError('serper_response_too_large')
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('error') or data.get('message') or data.get('statusCode',200) != 200:
            raise ValueError('serper_invalid_response')
        if not isinstance(data.get('organic'), list):
            raise ValueError('serper_invalid_organic')
        echo = data.get('searchParameters', {}).get('q') if isinstance(data.get('searchParameters', {}),dict) else None
        if echo is not None and (not isinstance(echo,str) or core.norm(echo)!=core.norm(task['query'])):
            raise ValueError('serper_query_echo_mismatch')
        hits, seen = [], set()
        for item in data['organic']:
            if not isinstance(item,dict) or not isinstance(item.get('link'),str) or not core.canonical(item['link']):
                raise ValueError('serper_invalid_url')
            url = core.canonical(item['link'])
            if url not in seen:
                seen.add(url)
                hits.append(dict(url=url, title=str(item.get('title') or ''),
                    engines=['google'], retrieved_by='serper', retrieved_at=core.utc()))
        return hits[:c['count']]
    except core.urllib.error.HTTPError as exc:
        # Never expose response bodies, which may echo request data.
        raise core.FetchProblem('serper_http_'+str(exc.code),
            retry=exc.code in {408,429} or exc.code>=500,
            delay=core.retry_after(exc.headers.get('Retry-After'))) from None
    except (OSError, core.urllib.error.URLError):
        raise core.FetchProblem('serper_network_error', retry=True) from None

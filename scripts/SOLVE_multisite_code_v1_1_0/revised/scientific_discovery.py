"""Waterbody-independent discovery of scientific publications and dataset pages."""
import json
import re
import time


DEFAULT_DOMAINS = [
    'ufz.de', 'igb-berlin.de', 'pangaea.de', 'copernicus.org',
    'link.springer.com', 'link.springernature.com', 'journals.plos.org',
    'onlinelibrary.wiley.com', 'sciencedirect.com',
]


def names(cfg, wb):
    values = [wb.get('query_name', wb['name']), wb['name']] + wb.get('aliases', [])
    seen, selected = set(), []
    for value in values:
        value = ' '.join(value.split()).strip('"')
        if value and value.casefold() not in seen:
            selected.append(value)
            seen.add(value.casefold())
    return selected[:cfg['scientific_discovery']['max_name_variants']]


def enabled(cfg):
    settings = cfg['scientific_discovery']
    return settings['enabled'] and settings['datacite_enabled']


def options(cfg, wb):
    for name in names(cfg, wb):
        yield name, 'dataset_metadata'


def literal_phrase(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def search(r, task, core):
    """Read public metadata only. Document downloads still pass normal LLM gates."""
    import quality_search
    if r.search_http_used >= r.cfg['limits']['max_searches_per_run']:
        raise core.FetchProblem('search_call_budget', retry=True)
    due = quality_search.metadata_next_try(r, 'datacite')
    if due > time.time():
        raise core.FetchProblem('datacite_cooldown', retry=True, delay=due-time.time())
    phrase = literal_phrase(task['query'])
    query = f'(titles.title:{phrase} OR descriptions.description:{phrase})'
    params = core.up.urlencode({'query': query, 'page[size]': r.cfg['search']['count']})
    req = core.ur.Request('https://api.datacite.org/dois?' + params,
                          headers={'Accept': 'application/vnd.api+json', 'User-Agent': r.cfg['user_agent']})
    time.sleep(r.cfg['search']['delay_seconds'])
    r.search_http_used += 1
    try:
        with core.ur.build_opener(core.NoRedirect()).open(req, timeout=r.cfg['search']['timeout_seconds']) as response:
            raw = response.read(4*1024*1024+1)
        if len(raw) > 4*1024*1024:
            raise ValueError('datacite_response_too_large')
        payload = json.loads(raw)
        rows = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError('datacite_invalid_items')
        hits, seen = [], set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('attributes'), dict):
                raise ValueError('datacite_invalid_item')
            record = row['attributes']
            titles, descriptions = record.get('titles') or [], record.get('descriptions') or []
            if not isinstance(titles, list) or not isinstance(descriptions, list):
                raise ValueError('datacite_invalid_metadata')
            title = ' / '.join(x['title'] for x in titles if isinstance(x, dict) and isinstance(x.get('title'), str))
            # Descriptions are read transiently for identity, never returned or persisted.
            description = ' '.join(x['description'] for x in descriptions if isinstance(x, dict) and isinstance(x.get('description'), str))
            if not core.identity_matches(r.bodies[task['profile']], title+' '+description):
                continue
            doi = record.get('doi', '')
            if not isinstance(doi, str) or not re.fullmatch(r'10\.\d{4,9}/[^\s<>]+', doi):
                continue
            landing = record.get('url')
            url = core.canonical(landing) if isinstance(landing, str) else None
            url = url or core.canonical('https://doi.org/'+doi)
            if url and url not in seen:
                seen.add(url)
                hits.append(dict(url=url, title=title, doi=doi, engines=['datacite'],
                                 retrieved_by='datacite', retrieved_at=core.utc()))
            if len(hits) >= r.cfg['search']['count']:
                break
        return hits
    except core.urllib.error.HTTPError as exc:
        raise core.FetchProblem('datacite_http_'+str(exc.code),
                                retry=exc.code in {408,429} or exc.code>=500,
                                delay=core.retry_after(exc.headers.get('Retry-After'))) from None
    except (OSError, core.urllib.error.URLError):
        raise core.FetchProblem('datacite_network_error', retry=True) from None

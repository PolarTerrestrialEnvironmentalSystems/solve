"""Discover title-matched scientific candidates; never mark them accepted.

Public DataCite/OpenAlex metadata only, without credentials, Serper or LLM calls.
All network starts share a <=1 request/second limiter. Raw metadata/cache and
auditable candidate output belong outside the source repository. Resume uses
successful cached requests. HTTP/auth/budget failures remain explicit.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from elbe_campaign import DEFAULT_REGISTRY, load_registry, name_aliases
from prepare_elbe_seeds import KNOWN_ALIASES, TOPICS, atomic_json, canonical, norm

DOCUMENTATION = {
    'datacite': 'https://support.datacite.org/docs/queries',
    'openalex': 'https://help.openalex.org/api/searching/',
    'openalex_limits': 'https://help.openalex.org/api/authentication/',
}


def utc():
    return datetime.now(timezone.utc).isoformat()


def discovery_names(body):
    """Include exact names plus explicitly labelled, non-authoritative search aids."""
    original = body['name_original']
    names = [original, *name_aliases(body), *KNOWN_ALIASES.get(body['waterbody_id'], [])]
    # A shorter lake name is only a search aid; subbasin/size identity is not
    # inherited. The later collector must verify the actual source independently.
    for name in list(names):
        base = re.sub(r'\s*\([^)]*\)\s*', ' ', name).strip()
        base = re.sub(r'^(?:GROSSER|GROẞER|GROßER|KLEINER|GROSSE|KLEINE)\s+', '', base, flags=re.I)
        if base != name and len(norm(base)) >= 6:
            names.append(base)
        if re.search(r'see$', base, re.I) and len(base) > 8 and ' ' not in base:
            names.append('Lake ' + base[:-3])
        match = re.match(r'^TALSPERRE\s+(.+)$', name, re.I)
        if match:
            names.extend([match[1] + ' Reservoir', 'Reservoir ' + match[1]])
    return list(dict.fromkeys(n for n in names if len(norm(n)) >= 4))[:12]


def query_for(provider, names):
    phrases = ' OR '.join('"' + re.sub(r'["\\]', ' ', name) + '"' for name in names)
    if provider == 'datacite':
        return 'https://api.datacite.org/dois?' + urlencode({
            'query': 'titles.title:(' + phrases + ')', 'page[size]': 100,
            'fields[dois]': 'doi,titles,url,publisher,publicationYear,types', 'sort': 'relevance'})
    return 'https://api.openalex.org/works?' + urlencode({
        'search.title': phrases, 'per_page': 100,
        'select': 'id,title,doi,primary_location,locations,publication_year,type,open_access'})


class MetadataClient:
    def __init__(self, output, interval=1.05, timeout=20, offline=False):
        self.root, self.interval, self.timeout = Path(output), max(1., interval), timeout
        self.offline = offline
        self.lock, self.last = threading.Lock(), 0.
        self.disabled, self.calls = {}, Counter()

    def get(self, provider, url):
        digest = hashlib.sha256(url.encode()).hexdigest()
        raw_path = self.root / 'raw' / (provider + '_' + digest + '.json')
        trace_path = self.root / 'requests' / (provider + '_' + digest + '.json')
        if raw_path.exists() and trace_path.exists():
            trace = json.loads(trace_path.read_text(encoding='utf8'))
            if trace.get('status') == 'ok':
                return json.loads(raw_path.read_text(encoding='utf8')), dict(trace, cached=True)
        if self.offline:
            if trace_path.exists():
                trace = json.loads(trace_path.read_text(encoding='utf8'))
                return None, dict(trace, cached=True)
            return None, dict(provider=provider, url=url, status='not_cached', cached=False)
        with self.lock:
            if provider in self.disabled:
                return None, dict(provider=provider, url=url, status='provider_paused', reason=self.disabled[provider])
            time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            self.calls[provider] += 1
        trace = dict(provider=provider, url=url, checked_at=utc(), cached=False)
        payload = None
        try:
            request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'SOLVE-scientific-discovery/1.0'})
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                trace.update(http_status=response.status, content_type=response.headers.get_content_type(),
                             response_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
                for field in ('X-RateLimit-Remaining', 'X-RateLimit-Credits-Used', 'Retry-After'):
                    if response.headers.get(field):
                        trace[field.lower()] = response.headers[field]
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError('metadata_response_too_large')
            payload = json.loads(raw)
            trace['status'] = 'ok'
            atomic_json(raw_path, payload)
        except HTTPError as exc:
            trace.update(status='http_error', http_status=exc.code)
            if exc.code in (401, 402, 403, 429):
                with self.lock:
                    self.disabled[provider] = 'http_' + str(exc.code)
                trace['provider_paused'] = True
        except (URLError, TimeoutError, ValueError, OSError) as exc:
            trace.update(status='request_error', error_type=type(exc).__name__)
        atomic_json(trace_path, trace)
        return payload, trace


def title_match(body, title):
    value = ' ' + norm(title) + ' '
    matched = [name for name in discovery_names(body) if ' ' + norm(name) + ' ' in value]
    if not matched or not TOPICS.search(title):
        return None
    exact = {norm(body['name_original']), *(norm(x) for x in name_aliases(body))}
    return dict(matched_title_term=matched[0], identity_scope='title_name_match_requires_source_confirmation',
                shortened_discovery_name=not any(norm(x) in exact for x in matched),
                technical_title_term=TOPICS.search(title).group(0))


def records(provider, payload):
    if provider == 'datacite':
        for item in payload.get('data', []):
            attr = item.get('attributes', {})
            title = ' | '.join(t['title'] for t in attr.get('titles', []) if t.get('title'))
            doi = attr.get('doi') or item.get('id')
            url = canonical(attr.get('url', '')) or canonical('https://doi.org/' + doi if doi else '')
            yield dict(title=title, doi=doi, metadata_id=item.get('id'), url=url,
                       publication_year=attr.get('publicationYear'), resource_type=attr.get('types', {}).get('resourceTypeGeneral'))
    else:
        for item in payload.get('results', []):
            locations = [item.get('primary_location') or {}, *(item.get('locations') or [])]
            pdfs = [canonical(x.get('pdf_url', '')) for x in locations]
            landings = [canonical(x.get('landing_page_url', '')) for x in locations]
            url = next((u for u in pdfs + landings + [canonical(item.get('doi', ''))] if u), None)
            yield dict(title=item.get('title', ''), doi=item.get('doi'), metadata_id=item.get('id'), url=url,
                       publication_year=item.get('publication_year'), resource_type=item.get('type'))


def candidates_for(body, provider, payload, trace):
    candidates, omitted = [], Counter()
    for record in records(provider, payload):
        evidence = title_match(body, record['title'])
        if not evidence:
            omitted['no_name_and_technical_topic_in_title'] += 1
            continue
        if not record['url']:
            omitted['no_public_document_url'] += 1
            continue
        candidates.append(dict(record, provider=provider + '_title_metadata', query=trace['url'],
            metadata_checked_at=trace['checked_at'], metadata_response_sha256=trace.get('response_sha256'),
            candidate_only=True, document_access_checked=False,
            same_name_requires_location_check=bool(body.get('_scientific_ambiguous_name')),
            validation_basis='Scientific metadata title only; HTTP and full source identity not validated', **evidence))
    total = payload.get('meta', {}).get('total' if provider == 'datacite' else 'count', 0)
    return candidates, dict(returned_records=len(list(records(provider, payload))), total_reported=total,
                            first_page_only=True, truncated=total > 100, omitted=dict(omitted))


def deduplicate(candidates):
    """One URL per DOI/work and per normalized title: mirrors are not extra seeds."""
    parent = list(range(len(candidates)))
    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    seen = {}
    for index, row in enumerate(candidates):
        doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', row.get('doi') or '', flags=re.I).lower()
        title = norm(row.get('title', ''))
        for key in [('url', row['url']), ('doi', doi), ('title', title)]:
            if not key[1]:
                continue
            if key in seen:
                parent[root(index)] = root(seen[key])
            else:
                seen[key] = index
    groups = defaultdict(list)
    for index, row in enumerate(candidates):
        groups[root(index)].append(row)
    def preference(row):
        kind = norm(row.get('resource_type') or '')
        return (0 if kind in {'article', 'journalarticle', 'journal article'} else 1,
                0 if '.pdf' in row['url'].lower() else 1,
                1 if 'doi.org/' in row['url'] else 0,
                row['url'], row.get('provider', ''), row.get('title', ''))
    chosen = [min(rows, key=preference) for rows in groups.values()]
    return sorted(chosen, key=lambda row:(norm(row['title']), row['url']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--waterbody', action='append', help='Exact official ID; repeat for a probe')
    parser.add_argument('--providers', choices=['datacite', 'openalex'], nargs='+', default=['datacite', 'openalex'])
    parser.add_argument('--interval', type=float, default=1.05)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--offline', action='store_true', help='Rebuild candidates only from saved metadata; no network requests')
    args = parser.parse_args(argv)
    if args.interval < 1 or args.workers < 1 or args.workers > 8:
        parser.error('At least 1 second request spacing; workers must be 1–8')
    bodies = [b for b in load_registry(args.registry) if b['waterbody_type'] == 'lake'
              and (not args.waterbody or b['waterbody_id'] in args.waterbody)]
    shared_names = Counter(norm(body['name_original']) for body in bodies)
    for body in bodies:
        body['_scientific_ambiguous_name'] = shared_names[norm(body['name_original'])] > 1
    client = MetadataClient(args.output, args.interval, offline=args.offline)
    candidates, report = defaultdict(list), []
    # Equal queries are fetched once, but identity is still reviewed per official ID.
    by_query = defaultdict(list)
    for body in bodies:
        for provider in args.providers:
            by_query[(provider, query_for(provider, discovery_names(body)))].append(body)
    def save():
        clean = {body['waterbody_id']: deduplicate(candidates[body['waterbody_id']]) for body in bodies}
        atomic_json(args.output / 'candidate_links.json', clean)
        atomic_json(args.output / 'summary.json', dict(created_at=utc(), registry_sha256=hashlib.sha256(args.registry.read_bytes()).hexdigest(),
            title_topic_pattern_sha256=hashlib.sha256(TOPICS.pattern.encode()).hexdigest(),
            documentation=DOCUMENTATION, requested_lakes=len(bodies), queries_total=len(by_query), queries_finished=len(report),
            mode='offline_metadata_replay' if args.offline else 'live_or_cached_metadata',
            calls=dict(client.calls), paused_providers=dict(client.disabled), candidate_links=sum(map(len, clean.values())),
            lakes_with_candidates=sum(bool(x) for x in clean.values()),
            note='Candidates only. Not accepted seeds. Title-only retrieval can miss works whose lake occurs only in abstract/body. No paid APIs/keys used. No automatic DOI/mirror count inflation.',
            waterbodies=[dict(waterbody_id=b['waterbody_id'], name=b['name_original'], candidates=len(clean[b['waterbody_id']])) for b in bodies],
            requests=report))
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(client.get, provider, query): (provider, query, rows) for (provider, query), rows in by_query.items()}
        for future in as_completed(futures):
            provider, query, rows = futures[future]
            payload, trace = future.result()
            trace['waterbody_ids'] = [b['waterbody_id'] for b in rows]
            if payload is not None:
                for body in rows:
                    found, details = candidates_for(body, provider, payload, trace)
                    candidates[body['waterbody_id']].extend(found)
                    trace.update(details)
            report.append(trace)
            if len(report) % 25 == 0:
                save()
                print(f'Metadata queries {len(report)}/{len(by_query)}; candidate lakes {sum(bool(x) for x in candidates.values())}; paused {client.disabled}', flush=True)
    save()
    print(json.dumps({'requested_lakes':len(bodies), 'calls':dict(client.calls), 'paused':client.disabled,
                      'candidate_links':sum(len(deduplicate(x)) for x in candidates.values())}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

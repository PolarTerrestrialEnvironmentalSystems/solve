"""Bounded, interleaved pilot with shared first-response snapshots.

Each strategy gets 4 requests per lake and up to 3 source attempts per request.
The same review rules and link ranking apply to all strategies. Remaining tasks
are deferred explicitly for this experiment, not silently counted as reviewed.
"""
import argparse
import json
import shutil
import time
from pathlib import Path
from unittest.mock import patch
import run_web_enrichment as s
import adaptive_search as adaptive

STRATEGIES = ['baseline', 'staged_greedy', 'staged_epsilon']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--rounds', type=int, default=4)
    parser.add_argument('--replay-only', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cache_dir = out / 'snapshots'
    cache_dir.mkdir(exist_ok=True)
    dossiers = out / ('replay_dossiers' if args.replay_only else 'dossiers')
    if dossiers.exists():
        raise SystemExit('Use a fresh experiment/replay dossier directory; never append a second experiment.')
    cfgs = {k: s.load_config(root / f'inputs/pilot_5_{k}.json') for k in STRATEGIES}
    common_cfg = json.loads(json.dumps(cfgs['baseline']))
    common_cfg['limits'].update(max_fetches_per_run=10000, max_urls_per_host_per_run=10000)
    common_store = s.Store(out / 'shared_sources')
    common_fetch = s.Fetcher(common_store, common_cfg)
    original_search = s.searxng_search
    cells = {}
    records = []
    log = out / ('replay_events.jsonl' if args.replay_only else 'events.jsonl')

    def cached_search(cfg, query):
        path = cache_dir / ('search_' + s.stable([cfg['search']['base_url'], cfg['search']['language'], cfg['search']['count'], query]) + '.json')
        if path.exists():
            data = json.loads(path.read_text(encoding='utf-8'))
        else:
            if args.replay_only:
                raise RuntimeError('Missing frozen search: ' + query)
            started = time.monotonic()
            try:
                hits, warnings = original_search(cfg, query)
                data = dict(hits=hits, warnings=warnings, error=None)
            except (s.FetchProblem, ValueError) as error:
                data = dict(error=str(error), retry=getattr(error, 'retry', False))
            data.update(seconds=time.monotonic()-started, captured_at=s.utc(), query=query)
            s.dump(path, data)
        if data['error']:
            raise s.FetchProblem(data['error'], retry=data.get('retry', False))
        return data['hits'], data['warnings']

    def attach_fetch(r):
        def fetch(url, refresh=False):
            # Snapshots freeze both successful access and failures for equal exposure.
            path = cache_dir / ('fetch_' + s.stable(url) + '.json')
            r.fetcher.used += 1
            host = s.host_of(url)
            r.fetcher.host_uses[host] = r.fetcher.host_uses.get(host, 0) + 1
            if path.exists():
                data = json.loads(path.read_text(encoding='utf-8'))
            else:
                if args.replay_only:
                    raise RuntimeError('Missing frozen URL: ' + url)
                started = time.monotonic()
                try:
                    source = common_fetch.fetch(url)
                    data = dict(source=source, error=None)
                except s.FetchProblem as error:
                    data = dict(error=str(error), retry=error.retry)
                data.update(seconds=time.monotonic()-started, captured_at=s.utc(), url=url)
                s.dump(path, data)
            if data['error']:
                raise s.FetchProblem(data['error'], retry=data.get('retry', False))
            source = data['source']
            target = r.store.root / source['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(common_store.root / source['path'], target)
            cols = list(source)
            r.store.db.execute('INSERT OR REPLACE INTO urls (' + ','.join(cols) + ') VALUES (' + ','.join('?' for _ in cols) + ')', [source[k] for k in cols])
            r.store.db.commit()
            return source
        r.fetcher.fetch = fetch

    try:
        for strategy, cfg in cfgs.items():
            for wb in cfg['waterbodies']:
                single = dict(cfg, waterbodies=[wb])
                r = s.Research(single, dossiers / strategy / wb['id'])
                attach_fetch(r)
                r.plan()
                cells[(strategy, wb['id'])] = r
        with patch.object(s, 'searxng_search', side_effect=cached_search):
            for round_index in range(args.rounds):
                # Rotate which strategy receives the first live exposure.
                order = STRATEGIES[round_index % 3:] + STRATEGIES[:round_index % 3]
                for wb in cfgs['baseline']['waterbodies']:
                    for strategy in order:
                        r = cells[(strategy, wb['id'])]
                        started = time.monotonic()
                        query = r.next_search()
                        if query is None:
                            records.append(dict(strategy=strategy, lake=wb['name'], round=round_index+1, skipped=True))
                            continue
                        print(f'{round_index+1}/{args.rounds} {strategy}: {wb["name"]} | {query["query"]}', flush=True)
                        r.do_search(query)
                        attempted = set()
                        for _ in range(3):
                            task = r.next_task(attempted)
                            if not task:
                                break
                            attempted.add(task['id'])
                            print('  source: ' + task['url'], flush=True)
                            r.process(task)
                            r.processed += 1
                        # Equal review budget; no claim that all returned sources were assessed.
                        r.store.db.execute("UPDATE tasks SET status='deferred',reason='pilot_review_budget' WHERE status IN ('pending','retry')")
                        r.store.db.commit()
                        current = r.store.one('SELECT * FROM searches WHERE id=?', (query['id'],))
                        record = dict(strategy=strategy, lake=wb['name'], wb=wb['id'], round=round_index+1,
                                      query=query['query'], query_id=query['id'], family=query['family'], reason=query['reason'],
                                      status=current['status'], source_attempts=len(attempted), wall_seconds=time.monotonic()-started)
                        records.append(record)
                        with log.open('a', encoding='utf-8') as handle:
                            handle.write(json.dumps(record, ensure_ascii=False)+'\n')
                        s.dump(out / ('replay_progress.json' if args.replay_only else 'progress.json'), records)
        summary = []
        for (strategy, wb_id), r in cells.items():
            stats, covered = adaptive.feedback(r)
            sources = r.store.rows('''SELECT t.url,t.status,t.reason,t.branch,u.sha,u.path,v.rule_response FROM tasks t
                LEFT JOIN urls u ON u.url=t.url LEFT JOIN reviews v ON v.id=t.review_id''')
            relevant = []
            for source in sources:
                if source['rule_response'] and json.loads(source['rule_response'])['decision']=='relevant':
                    relevant.append(dict(url=source['url'], sha=source['sha'], path=source['path'], topics=json.loads(source['rule_response'])['topic_hits']))
            searches = r.store.rows('SELECT id,query,status,family,results FROM searches WHERE status NOT IN (\'pending\',\'deferred_budget\')')
            item = dict(strategy=strategy, wb=wb_id, name=r.cfg['waterbodies'][0]['name'], searches=len(searches),
                source_attempts=r.processed, reviewed=sum(bool(x['rule_response']) for x in sources),
                unique_archived=len({x['sha'] for x in sources if x['sha']}),
                relevant_unique=len({x['sha'] for x in relevant}),
                topics=sorted(set().union(*covered.values()) if covered else set()),
                unreviewed_nonbudget_tasks=sum(x['reason']!='pilot_review_budget' and not x['rule_response'] for x in sources),
                partial_searches=len(r.store.rows("SELECT * FROM events WHERE kind='search_engine_failures'")),
                relevant_sources=relevant, feedback=list(stats.values()))
            summary.append(item)
            r.export()
        s.dump(out / ('replay_summary.json' if args.replay_only else 'summary.json'), dict(checked_at=s.utc(), rounds=args.rounds,
            method='Interleaved pilot; shared first-response snapshots; 3 source attempts per search; identical hardened reviewer for all policies',
            limitations=['No manually labelled gold standard', 'Single small pilot; no significance claim',
                          'Cache advantages make observed wall times incomparable as standalone speed',
                          'Source review is capped; unchanged document bytes can still be separate mirrors semantically',
                          'Partial engine failures limit recall'], results=summary))
        print('COMPLETED ' + str(out), flush=True)
    finally:
        for r in cells.values():
            r.close()
        common_store.close()


if __name__ == '__main__':
    main()

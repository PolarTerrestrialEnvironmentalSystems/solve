"""Paired chunk-size pilot; production code remains unchanged."""
from pathlib import Path
import sys, time, json
from collections import Counter

ROOT = Path(__file__).resolve().parent
CODE = ROOT.parent.parent / 'SOLVE_Parsing_Fein_Batch_1.2.0'
sys.path.insert(0, str(CODE))
from common import load_env, read_json, write_json, read_jsonl, digest, file_hash, utc_now, RunLock
from blablador_client import Settings, BlabladorClient
from extract_facts import extract_chunk, context_jobs, SYSTEM_PROMPT
from prepare_document import make_chunks, prepare_document, normalize_quote
from schemas import Event, Relationship, Extraction, read_headers, extraction_schema
from validate_export import finalize, export_variant, checks

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    previous = read_json(ROOT.parent / 'repeated_self_relations/prepared_all.json')
    prepared = prepare_document(Path(previous['document']['local_file']), ROOT/'prepared', '10,11,17', 4000)
    prepared['blocks'] = [b for b in prepared['blocks'] if b['page'] in {10,11,17}]
    old_text = normalize_quote('\n'.join(b['text'] for b in previous['blocks']))
    new_text = normalize_quote('\n'.join(b['text'] for b in prepared['blocks']))
    assert old_text == new_text, 'Source text changed beyond paragraph boundaries'
    write_json(ROOT/'source_equivalence.json', {'normalized_text_identical':True, 'old_blocks':len(previous['blocks']), 'new_blocks':len(prepared['blocks'])})
    write_json(ROOT/'prepared_all.json', prepared)
    blocks = prepared['blocks']
    sizes = [4000]
    plan = {'sizes': sizes, 'orders': [sizes, list(reversed(sizes))], 'repeats': 2,
            'blocks': blocks, 'document': prepared['document'], 'cover': [],
            'source_chars': sum(len(b['text']) for b in blocks),
            'source_sha256': file_hash(Path(prepared['document']['local_file'])),
            'context_calls_max': 2,
            'method': 'Parser 1.2.0, fresh paragraph-preserving preparation; normalized source text verified identical to prior study. Soft 4000-character target. Comparison changes preprocessing, prompt, quantity schema and quality repair together. Native neighbor context, semantic repair, split recovery and up to two context calls enabled. Sequential requests, fresh outputs, reversed order. PDF preparation and model-list request excluded from timed units; extraction, retries, repair, validation and export included.',
            'limitations': 'One document, selected noncontiguous pages 10/11/17, no image interpretation. Two repeats; not a production-wide optimum. No full-document context outside selected blocks.',
            'quality_checklist': [
                'Moldau QM364: 20.0 (1924-1953) -> 38.8 m3/s (1955-1993), dam influence',
                'Moldau QM364: 36.9 m3/s (1961-2005)',
                'Berounka contribution: 3.8 m3/s within 38.8',
                'Moldau cascade: nine dams, storage 1352.58 million m3',
                'Vrane minimum release: 40.0 m3/s',
                'Prague before dams: flows 12.0-15.0 m3/s',
                'Nechranice: 272.43 million m3, influence from 1966, official commissioning 1968',
                'Eger Louny QM364: 2.7 (1922-1965) -> 8.0 m3/s (1966-1993), dam influence',
                'Jesenice: 52.75 million m3, commissioned 1961',
                'Skalka: 15.92 million m3, commissioned 1964',
                'Nechranice minimum release: 8.0 m3/s',
                'Eger low-flow increase also influences Elbe below confluence',
                'Czech Elbe mean annual discharge projected -10%, 2009-2053 versus 1961-2005',
                'Moldau mean discharge projected -10 to -15%',
                'Winter precipitation/rain increase -> more winter discharge/floods (projection)',
                'Lower summer rain -> lower surface/subsurface runoff -> worse summer low flow (projection)',
                'Border catchment 51394 km2; dam-controlled 21372 km2 / 41.6%',
                'Future conflict: flood retention space versus water storage for low-flow release',
                'Agreed minimum monthly border flows likely undershot more often, especially summer; prediction, not observation'
            ]}
    if (ROOT/'plan.json').exists():
        assert read_json(ROOT/'plan.json') == plan
    else:
        write_json(ROOT/'plan.json', plan)
    env = {}; load_env(Path(r'C:\Users\jowals001\awi\solve\scripts\graphrag\data\.env'), env)
    settings = Settings(api_key=env['GRAPHRAG_API_KEY2'], rpm=30, timeout=300, max_retries=1,
                        max_output_tokens=16000, context_tokens=65536, temperature=0, thinking_mode='off')
    with RunLock(ROOT/'.study.lock'):
        client = BlabladorClient(settings, ROOT/'setup_logs', ROOT/'api_attempts.jsonl', 'setup')
        try: snapshot = client.models()
        finally: client.close()
        write_json(ROOT/'models.json', snapshot)
        identity = next(m for m in snapshot['data'] if m.get('root') == 'nvidia/Qwen3.8-Flash-Next-NVFP4' and not m['id'].startswith('alias-'))
        settings.model = identity['id']
        ontology = read_json(CODE/'ontologie_fein.json')
        headers = {'events': read_headers(CODE/'Event_headers.xlsx', Event),
                   'relationships': read_headers(CODE/'relationship_headers.xlsx', Relationship)}
        for repeat, order in enumerate(plan['orders'], 1):
            for size in order:
                name = f'r{repeat}_chars{size}'
                output = ROOT/'results'/name
                if (output/'result.json').exists():
                    print('Already completed (not remeasured):', name, flush=True); continue
                if output.exists():
                    raise RuntimeError(f'Incomplete run exists: {output}; do not mix cache and fresh timing')
                chunks = make_chunks(blocks, size)
                assert [b for c in chunks for b in c['blocks']] == blocks
                config = {'plan_hash': digest(plan), 'settings': settings.public(), 'model': identity,
                          'code_hashes': {p.name:file_hash(p) for p in sorted(CODE.glob('*.py'))},
                          'controller_hash':file_hash(Path(__file__)), 'prompt_hash':digest(SYSTEM_PROMPT),
                          'schema_hash':digest(extraction_schema(ontology)), 'ontology_hash':file_hash(CODE/'ontologie_fein.json'),
                          'chunks': chunks, 'repeat':repeat, 'size':size}
                signature = digest(config)
                write_json(output/'manifest.json', {'started':utc_now(), 'config':config, 'signature':signature})
                client = BlabladorClient(settings, output/'logs', ROOT/'api_attempts.jsonl', name)
                client.configure_models(snapshot)
                packages = []
                print('START', name, 'chunks', len(chunks), flush=True)
                start = time.perf_counter()
                try:
                    for chunk in chunks:
                        packages.extend(extract_chunk(client, chunk, [], ontology, headers, output/'fein', signature, 'fein'))
                        if any(p['status'] != 'complete' for p in packages):
                            write_json(output/'aborted.json', {'reason':'Incomplete extraction; stop experiment instead of comparing missing outputs', 'packages':packages, 'elapsed_seconds':time.perf_counter()-start})
                            raise RuntimeError('Benchmark stopped: incomplete extraction. Inspect provider logs before starting a fresh trial.')
                    jobs, extra = context_jobs(packages, blocks, plan['context_calls_max'])
                    for job in jobs:
                        packages.extend(extract_chunk(client, job['chunk'], [], ontology, headers, output/'fein', signature, 'fein', job['existing_events'], purpose='context'))
                finally: client.close()
                events, rels, review, summary = finalize(packages, ontology, plan['document'], signature, 'fein', extra)
                export_variant(output/'fein', events, rels, review, summary, headers)
                elapsed = time.perf_counter()-start
                rows = [(p,kind,r) for p in packages if p['status']=='complete' for kind in ['events','relationships'] for r in p['result'][kind]]
                errors = Counter()
                for p,kind,row in rows:
                    fatal, warnings = checks(row, ontology, {b['block_id']:b for b in p['supplied_blocks']},kind=='events')
                    errors.update(fatal)
                logs = read_jsonl(output/'logs/api_requests.jsonl')
                finished = [r for r in logs if r['event']=='request_finished']
                checkpoints = [read_json(p) for p in (output/'fein/checkpoints').glob('*.json')]
                result = {'name':name, 'size':size, 'repeat':repeat, 'elapsed_seconds':elapsed,
                          'initial_chunks':len(chunks), 'primary_chars':[sum(len(b['text']) for b in c['blocks']) for c in chunks],
                          'packages':packages, 'all_complete':all(p['status']=='complete' for p in packages),
                          'raw_events':sum(kind=='events' for _,kind,_ in rows),
                          'raw_relationships':sum(kind=='relationships' for _,kind,_ in rows),
                          'exported_events':len(events), 'exported_relationships':len(rels),
                          'fatal_row_reasons':dict(errors), 'context_calls':len(jobs),
                          'split_reasons':dict(Counter(c['reason'] for c in checkpoints if 'reason' in c)),
                          'http_attempts':sum(r['event']=='request_started' for r in logs),
                          'request_purposes':dict(Counter(r.get('purpose') for r in logs if r['event']=='request_started')),
                          'completion_tokens':sum((r.get('usage') or {}).get('completion_tokens',0) for r in finished),
                          'prompt_tokens':sum((r.get('usage') or {}).get('prompt_tokens',0) for r in finished),
                          'finished':utc_now()}
                write_json(output/'result.json', result)
                print(f'DONE {name}: {elapsed:.1f}s, raw {result["raw_events"]}/{result["raw_relationships"]}, export {len(events)}/{len(rels)}, calls {result["http_attempts"]}', flush=True)

if __name__ == '__main__': main()

"""Build metrics and a source-linked, compact manual review view."""
from pathlib import Path
import json, sys, statistics
from collections import Counter
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent.parent/'SOLVE_Parsing_Fein_Batch_1.1.0'))
from common import read_json, read_jsonl, write_json, atomic_text
from validate_export import checks
ontology=read_json(ROOT.parent.parent/'SOLVE_Parsing_Fein_Batch_1.1.0/ontologie_fein.json')
metrics=[]
for path in sorted((ROOT/'results').glob('*/result.json')):
    r=read_json(path)
    metrics.append({k:v for k,v in r.items() if k!='packages'})
    lines=[f'# {r["name"]}', 'IDs below are scoped to their package; OK means automated validation passed, not a semantic quality judgment.']
    seen=set()
    for p in r['packages']:
        if p['status']!='complete':
            lines.append(f'FAILED: {p}'); continue
        lines.append('\n## '+p['chunk_id'])
        blocks={b['block_id']:b for b in p['supplied_blocks']}
        for kind in ['events','relationships']:
            for row in p['result'][kind]:
                fatal,warnings=checks(row,ontology,blocks,kind=='events')
                key=row.get('event_id',row.get('relationship_id'))
                lines.append(f'\n{key} [{row["assertion_mode"]}] {row["statement"]}')
                if kind=='relationships': lines.append(f'EDGE {row["source_event_id"]} {row["relationship_type"]} {row["target_event_id"]}')
                else:
                    lines.append('VALUES '+json.dumps(row.get('values'),ensure_ascii=False))
                    lines.append('TIME '+json.dumps([row.get('time_original'),row.get('time_normalized')],ensure_ascii=False))
                lines.append('VALIDATION '+(', '.join(fatal) if fatal else 'OK'))
                lines.append('EVIDENCE '+json.dumps(row['evidence'],ensure_ascii=False))
    atomic_text(path.parent/'quality_review.txt','\n'.join(lines))
write_json(ROOT/'metrics.json',metrics)
aggregates=[]
for size in sorted({r['size'] for r in metrics}):
    runs=[r for r in metrics if r['size']==size]
    aggregates.append({'size':size,'n':len(runs),'mean_seconds':statistics.mean(r['elapsed_seconds'] for r in runs),
                       'min_seconds':min(r['elapsed_seconds'] for r in runs),'max_seconds':max(r['elapsed_seconds'] for r in runs),
                       'mean_exported_events':statistics.mean(r['exported_events'] for r in runs),
                       'mean_exported_relationships':statistics.mean(r['exported_relationships'] for r in runs),
                       'http_attempts':[r['http_attempts'] for r in runs]})
write_json(ROOT/'aggregates.json',aggregates)
print(json.dumps(aggregates,indent=2))

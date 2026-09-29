from pathlib import Path
import json
import csv
import statistics
import sys
from collections import Counter
ROOT=Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding='utf-8')

def read(p):return json.loads(p.read_text(encoding='utf-8'))
rows=[read(p) for p in sorted((ROOT/'results').glob('*/result.json'))]
summary={}
for variant in ['reference','compact']:
    group=[r for r in rows if r['variant']==variant]
    if not group:continue
    summary[variant]={'runs_recorded':len(group),'successful_runs':sum(r['all_packages_complete'] for r in group),
        'runs_with_raw_self':sum(r['raw_self_relationships']>0 for r in group),
        'runs_with_exported_self':sum(r['exported_self_relationships']>0 for r in group),
        'raw_self_edges':sum(r['raw_self_relationships'] for r in group),
        'raw_edges':sum(r['raw_relationships'] for r in group),
        'exported_self_edges':sum(r['exported_self_relationships'] for r in group),
        'exported_edges':sum(r['exported_relationships'] for r in group),
        'zero_raw_edge_runs':sum(r['raw_relationships']==0 for r in group),
        'zero_exported_edge_runs':sum(r['exported_relationships']==0 for r in group),
        'http_attempts':sum(r['http_attempts'] for r in group),
        'elapsed_seconds':sum(r['elapsed_seconds'] for r in group),
        'median_run_seconds':statistics.median(r['elapsed_seconds'] for r in group),
        'completion_tokens':sum(r['completion_tokens'] for r in group),
        'cases':{}}
    for case in sorted({r['case'] for r in group}):
        g=[r for r in group if r['case']==case]
        normalized=[json.dumps([p.get('result') for p in r['packages']],sort_keys=True,ensure_ascii=False) for r in g]
        summary[variant]['cases'][case]={'runs':len(g),'raw_self_runs':sum(r['raw_self_relationships']>0 for r in g),
             'exported_self_runs':sum(r['exported_self_relationships']>0 for r in g),
             'raw_edges':sum(r['raw_relationships'] for r in g),'exported_edges':sum(r['exported_relationships'] for r in g),
             'unique_normalized_answers':len(set(normalized))}
(ROOT/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
columns=['name','variant','repeat','case','all_packages_complete','raw_events','raw_relationships','raw_self_relationships',
         'exported_events','exported_relationships','exported_self_relationships','elapsed_seconds','completion_tokens','http_attempts']
with (ROOT/'run_metrics.csv').open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows({k:r[k] for k in columns} for r in rows)
examples=[]
for r in rows:
    for package in r['packages']:
        if package['status']!='complete':continue
        events={e['event_id']:e for e in package['result']['events']}
        for edge in package['result']['relationships']:
            if edge['source_event_id']==edge['target_event_id']:
                examples.append({'run':r['name'],'stage':'schema_valid_model_output','edge':edge,'event':events.get(edge['source_event_id'])})
    for edge in r['exported_self_examples']:
        examples.append({'run':r['name'],'stage':'final_export','edge':edge})
(ROOT/'self_relation_examples.json').write_text(json.dumps(examples,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))

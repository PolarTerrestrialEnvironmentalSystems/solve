import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent
p = root / 'inputs/sites_blablador.json'
c = json.loads(p.read_text(encoding='utf-8'))
for w in c['waterbodies']:
    if w['name'] == 'Arendsee':
        w['ambiguous_place_name'] = True
    if w['name'] == 'Rappbodetalsperre':
        w['identity_exclusions'] = ['Rappbodetalsperre/Vorsperre Hassel', 'Rappbodetalsperre/Vorsperre Rappbode']
p.write_text(json.dumps(c, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
names = ['Arendsee', 'Bergwitzsee', 'Barleber See I', 'Barleber See II', 'Rappbodetalsperre/Vorsperre Hassel']
byname = {w['name']: w for w in c['waterbodies']}
for strategy in ['baseline', 'staged_greedy', 'staged_epsilon']:
    b = json.loads(json.dumps(c))
    b['waterbodies'] = [byname[n] for n in names]
    b['search'].update(strategy=strategy, epsilon=.2, adaptive_max_queries=12, plateau_queries=4)
    b['limits'].update(max_searches_per_run=20, max_seconds_per_run=1200, max_fetches_per_run=60, max_tasks_per_run=60, max_depth=2, max_pdf_pages=100, parse_timeout_seconds=15)
    (root / f'inputs/pilot_5_{strategy}.json').write_text(json.dumps(b, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

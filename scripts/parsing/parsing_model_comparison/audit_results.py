from pathlib import Path
import json
import sys
sys.stdout.reconfigure(encoding='utf-8')
ROOT=Path(__file__).resolve().parent
for variant in ['reference','compact_qwen','compact_gptoss']:
    folder=ROOT/'results'/variant
    print('\nVARIANT',variant)
    events=[json.loads(s) for s in (folder/'fein/events.jsonl').read_text(encoding='utf-8').splitlines()]
    for e in events:
        print(json.dumps({k:e[k] for k in ['event_id','statement','time_original','time_normalized','values','assertion_mode','polarity']},ensure_ascii=False))
    print('REJECTIONS')
    for line in (folder/'fein/review_items.jsonl').read_text(encoding='utf-8').splitlines():
        row=json.loads(line)
        if row['kind'].startswith('rejected_'):
            c=row.get('candidate',{})
            print(json.dumps({'reasons':row['reasons'],'statement':c.get('statement'),'values':c.get('values')},ensure_ascii=False))

#!/usr/bin/env python3
"""One-command entry point from waterbody information and research context.

python start_research.py --input examples/arendsee.json --workspace my_dossier
If BRAVE_API_KEY is set, live search uses Brave; otherwise uses Blablador SearXNG.
The external provider is a hand-off, not an autonomous search engine.
"""
import argparse
import json
import os
from pathlib import Path
import run_web_enrichment as collector

def prepare_input(raw):
    sites=raw.get('waterbodies') or raw.get('sites')
    if not sites:
        raise ValueError('Provide waterbodies: [{name, region, context, keywords}]')
    if isinstance(sites,dict):sites=[sites]
    bodies=[]
    for site in sites:
        if not isinstance(site,dict) or not str(site.get('name','')).strip():
            raise ValueError('Each waterbody needs a name')
        body=dict(site)
        body.setdefault('id','site_'+collector.stable([site['name'],site.get('region','')])[:16])
        bodies.append(body)
    cfg=dict(raw,waterbodies=bodies)
    cfg.pop('sites',None)
    if not cfg.get('topics'):
        cfg['topics']=json.loads((Path(__file__).parent/'common_topics.json').read_text())
    cfg.setdefault('search',dict(provider='brave' if os.getenv('BRAVE_API_KEY') else 'searxng'))
    cfg.setdefault('review',dict(mode='rules'))
    cfg.setdefault('storage',dict(persist_extracted_text=False))
    cfg.setdefault('limits',{})
    cfg['limits'].setdefault('max_searches_per_run',max(12,len(bodies)))
    return cfg

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True)
    p.add_argument('--workspace',default='web_research_data')
    p.add_argument('--plan-only',action='store_true')
    args=p.parse_args()
    cfg=collector.load_config(prepare_input(json.loads(Path(args.input).read_text(encoding='utf-8-sig'))))
    root=Path(args.workspace);root.mkdir(parents=True,exist_ok=True)
    cfgfile=root/'resolved_config.json'
    collector.dump(cfgfile,cfg)
    return collector.main(['plan' if args.plan_only else 'run','--config',str(cfgfile),'--workspace',str(root)])

if __name__=='__main__':raise SystemExit(main())

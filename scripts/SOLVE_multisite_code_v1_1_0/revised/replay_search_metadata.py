#!/usr/bin/env python3
"""Reuse existing search evidence after an explicit context/profile revision.

This does NOT execute searches. Original request IDs and their replacements
are recorded in replay_mapping.jsonl. New identities remain unverified.
"""
import argparse
import json
from pathlib import Path
import run_web_enrichment as s

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True);p.add_argument('--workspace',required=True)
    p.add_argument('--responses',nargs='+',required=True)
    args=p.parse_args();cfg=s.load_config(args.config)
    rows={}
    for f in args.responses:
        for row in s.read_jsonl(f):
            rows[(row['waterbody_id'],row['query'])]=row
    with s.WorkspaceLock(args.workspace):
        r=s.Research(cfg,args.workspace)
        try:
            r.plan();mapped=[];mapping=[]
            for (wb,q),row in rows.items():
                if wb not in r.profiles:
                    raise ValueError('Response belongs to a site outside this configuration')
                family=row.get('family','overview')
                sid=r.add_search(r.profiles[wb],q,family,'Previously executed query, reused after context revision',100 if 'filetype:pdf' in q else 90)
                mapped.append(dict(row,search_id=sid))
                mapping.append(dict(waterbody_id=wb,query=q,original_search_id=row['search_id'],active_search_id=sid))
            r.store.db.commit()
            out=r.store.root/'replayed_search_responses.jsonl';s.jsonl(out,mapped)
            s.jsonl(r.store.root/'replay_mapping.jsonl',mapping)
            print('Reused search responses:',r.import_searches(out))
            r.reset('reassess')
            r.export()
        finally:r.close()

if __name__=='__main__':main()

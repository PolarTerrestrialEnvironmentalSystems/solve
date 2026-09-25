#!/usr/bin/env python3
"""Create the measured, reproducible multisite report from actual artifacts."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'revised'))
import run_web_enrichment as s

def read_rows(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]

def search_round(paths):
    out={}
    for p in paths:
        for r in read_rows(p):out[r['waterbody_id']]=r
    return out

def run(dossier):
    cfg=s.load_config(ROOT/'inputs/sites_final.json')
    base=search_round([ROOT/'inputs/baseline_search_responses.jsonl',ROOT/'inputs/baseline_search_retries.jsonl'])
    second=search_round([ROOT/'inputs/revised_search_responses.jsonl'])
    bodies={w['id']:w for w in cfg['waterbodies']}
    all_rows=read_rows(dossier/'results/alle_quellen_inkl_verworfene.jsonl')
    by_site=defaultdict(list)
    for row in all_rows:by_site[row['waterbody_id']].append(row)
    conn=sqlite3.connect(dossier/'research.sqlite');conn.row_factory=sqlite3.Row
    sources={r['url']:dict(r) for r in conn.execute('SELECT * FROM urls')}
    inventory={}
    def add(url,title,wb,stage):
        url=s.canonical(url)
        item=inventory.setdefault(url,dict(url=url,title=title,sites=set(),stages=set()))
        if not item['title']:item['title']=title
        item['sites'].add(wb);item['stages'].add(stage)
    sets=[]
    for label,data in [('baseline_search',base),('pdf_search',second)]:
        urls=set()
        for wb,row in data.items():
            for hit in row['results']:
                add(hit['url'],hit['title'],wb,label);urls.add(s.canonical(hit['url']))
        sets.append(urls)
    for wb,body in bodies.items():
        for url in body['seed_urls']:add(url,'Register seed',wb,'register_seed')
    for row in all_rows:
        if row['original_bytes']:add(row['url'],row['title'],row['waterbody_id'],'archived')
    site_rows=[]
    for wb,body in bodies.items():
        a={s.canonical(h['url']) for h in base[wb]['results']}
        b={s.canonical(h['url']) for h in second[wb]['results']}
        rows=by_site[wb]
        archived={r['sha256'] for r in rows if r['original_bytes']}
        def hashes(decisions):return {r['sha256'] for r in rows if r['original_bytes'] and r['decision'] in decisions}
        site_rows.append(dict(site_id=wb,original_label=body['original_label'],search_name=body['name'],region=body['region'],
          identity_requires_confirmation=body['identity_requires_confirmation'],
          baseline_search_candidates=len(a),pdf_pass_candidates=len(b),additional_pdf_pass_candidates=len(b-a),
          combined_search_candidates=len(a|b),register_seeds=len(set(body['seed_urls'])),
          archived_originals_available=len(archived),relevance_screened_originals=len(hashes({'relevant','supporting','uncertain','irrelevant'})),
          provisionally_relevant=len(hashes({'relevant','supporting'})),uncertain=len(hashes({'uncertain'})),rejected=len(hashes({'irrelevant'})),
          pending_crawl_tasks=sum(r['task_status'] in {'pending','retry','deferred'} for r in rows),
          context=body['context'],keywords='; '.join(body['keywords'])))
    index=[]
    for url,item in sorted(inventory.items()):
        src=sources.get(url,{})
        records=[r for wb in item['sites'] for r in by_site[wb] if r['url']==url]
        index.append(dict(url=url,title=item['title'],site_ids='; '.join(sorted(item['sites'])),stages='; '.join(sorted(item['stages'])),
          pdf_url_hint='.pdf' in url.casefold(),retrieval_status=src.get('status','not_attempted'),
          original_file=src.get('path') if src.get('original') else '',sha256=src.get('sha') or '',
          decisions_by_site=json.dumps({r['waterbody_id']:r['decision'] for r in records},ensure_ascii=False),
          identity_warning_sites='; '.join(w for w in sorted(item['sites']) if bodies[w]['identity_requires_confirmation']),
          access_issue=src.get('error') or ''))
    s.write_csv(ROOT/'SITE_OVERVIEW.csv',site_rows)
    s.write_csv(ROOT/'SOURCE_INDEX.csv',index)
    s.jsonl(ROOT/'SOURCE_INDEX.jsonl',index)
    originals={r['sha']:r for r in sources.values() if r['status']=='ok' and r.get('original') and r.get('path')}
    kinds=Counter(Path(r['path']).suffix for r in originals.values())
    useful={r['sha256'] for r in all_rows if r['original_bytes'] and r['decision'] in {'relevant','supporting'}}
    uncertain={r['sha256'] for r in all_rows if r['original_bytes'] and r['decision']=='uncertain'}
    rejected={r['sha256'] for r in all_rows if r['original_bytes'] and r['decision']=='irrelevant'}
    parse_status=Counter()
    nameless_pdfs=[]
    for row in all_rows:
        if not row['original_bytes'] or not row.get('metadata_file'):continue
        meta=json.loads((dossier/row['metadata_file']).read_text())
        parse_status[meta['status']]+=1
        wb=bodies[row['waterbody_id']]
        if row['original_file'].endswith('.pdf') and row['decision'] in {'relevant','supporting'} and not s.identity_matches(wb,meta.get('title','')):
            nameless_pdfs.append(dict(site_id=wb['id'],name=wb['name'],url=row['url'],sha256=row['sha256'],title=meta.get('title',''),metadata_file=row['metadata_file']))
    checks=[]
    for sha,row in originals.items():
        file=dossier/row['path'];checks.append(dict(file=row['path'],sha256=sha,ok=file.exists() and hashlib.sha256(file.read_bytes()).hexdigest()==sha))
    privacy=[]
    for row in conn.execute('SELECT path FROM parses'):
        obj=json.loads((dossier/row['path']).read_text())
        if any(k in obj for k in ['text','fragments','links']):privacy.append(row['path'])
    for row in conn.execute('SELECT id,request,response FROM reviews'):
        req=json.loads(row['request']);resp=json.loads(row['response'] or '{}')
        if req.get('fragments') or any(x.get('context') for x in req.get('links',[])) or resp.get('evidence_quotes'):
            privacy.append('review:'+row['id'])
    runs=[dict(r) for r in conn.execute('SELECT started,ended,summary FROM runs ORDER BY started')]
    if any(not r['ended'] for r in runs):raise RuntimeError('Do not report while a run is still active')
    assert not privacy and not (dossier/'extracted').exists()
    assert all(r['ok'] for r in checks)
    counts=dict(target_sites=len(bodies),identity_warnings=sum(w['identity_requires_confirmation'] for w in bodies.values()),
      scheduled_site_searches=len(base)+len(second),sites_with_search_candidates=sum(bool(r['combined_search_candidates']) for r in site_rows),
      baseline_search_urls=len(sets[0]),pdf_pass_search_urls=len(sets[1]),combined_search_urls=len(sets[0]|sets[1]),
      additional_search_urls=len(sets[1]-sets[0]),baseline_pdf_url_hints=sum('.pdf' in u.casefold() for u in sets[0]),
      pdf_pass_pdf_url_hints=sum('.pdf' in u.casefold() for u in sets[1]),
      inventory_urls_including_seeds_and_archived=len(inventory),archived_unique_originals=len(originals),
      archived_pdfs=kinds['.pdf'],archived_html=kinds['.html'],provisionally_relevant_originals=len(useful),
      originals_with_uncertain_site_associations=len(uncertain),originals_with_rejected_site_associations=len(rejected),
      archived_sites=sum(r['archived_originals_available']>0 for r in site_rows),
      useful_pdfs_without_lake_in_embedded_title=len({r['sha256'] for r in nameless_pdfs}),
      privacy_failures=len(privacy),integrity_verified_originals=len(checks),parse_association_statuses=dict(parse_status),
      access_issues=dict(Counter(r.get('error') for r in sources.values() if r['status'] in {'blocked','retry'})),
      active_profile_task_states=dict(Counter(r['task_status'] for r in all_rows)))
    validation=ROOT/'validation';validation.mkdir(exist_ok=True)
    test_count=int(re.search(r'Ran (\d+) tests', (validation/'tests.txt').read_text()).group(1))
    s.dump(validation/'metrics.json',counts);s.dump(validation/'integrity.json',checks)
    s.dump(validation/'run_history.json',runs);s.dump(validation/'nameless_pdf_examples.json',nameless_pdfs)
    s.dump(validation/'additional_search_checks.json',dict(engine='web_search_system1',queries=['"Scharmühlenbach" Wittenberg Gewässer','"Scharmühlenbach"'],result='No results for either query',note='Additional checks; not imported as source documents'))
    def num(v):return f'{v:,}'
    lines=[
      '# SOLVE multisite test and revision — 22 September 2026','',
      f'The uploaded collector was applied to all **{counts["target_sites"]} register entries other than Arendsee**, revised, and rerun. Inputs contain site context and keywords plus 19 shared topic groups. The two search passes produced **{num(counts["combined_search_urls"])} distinct search candidate URLs**, of which **{num(counts["additional_search_urls"])} were added by the PDF-focused pass**.',
      '',f'Actual retrieval produced **{counts["archived_unique_originals"]} distinct original files: {counts["archived_pdfs"]} PDFs and {counts["archived_html"]} HTML pages**. Of these, **{counts["provisionally_relevant_originals"]} matched the preliminary relevance rules for at least one active site**. Search candidates are not counted as downloaded or verified documents.',
      '', '## Scope and inputs','',
      'The source register has 138 entries. Arendsee remains an example configuration; its example was only validated with `--plan-only`. The other entries include lakes, river reaches, ponds, wetlands and regional sites. The collector is generic; site names and regional information are configuration data.',
      '',f'The final input retains **{counts["identity_warnings"]} identity warnings**. Candidates such as spelling variants, large/small lake ambiguities, uncertain districts and possible duplicates are not silently merged. No new coordinates or official waterbody identifiers were invented. Register historical notes are search context, not newly verified findings.',
      '', 'The supplied ZIP contains the single-file **1.0.0** collector. The tested revision is **1.1.0**. The previously discussed modular 2.6.0 package was not the baseline for this task.',
      '', '## Measured comparison','',
      '| Measure | Baseline overview pass | Revised PDF-focused pass |',
      '|---|---:|---:|',
      f'| Executed site-level queries | {len(base)} | {len(second)} |',
      f'| Distinct candidate URLs from search | {num(len(sets[0]))} | {num(len(sets[1]))} |',
      f'| URLs containing a PDF filename hint | {counts["baseline_pdf_url_hints"]} | {counts["pdf_pass_pdf_url_hints"]} |',
      f'| Candidate URLs added beyond baseline | — | {num(counts["additional_search_urls"])} |',
      f'| Sites with at least one search result | {sum(bool(r["results"]) for r in base.values())} | {sum(bool(r["results"]) for r in second.values())} |',
      '', 'These are two complementary query strategies, not a controlled causal estimate of code quality. Both used the same search engine and retained at most eight results per site per pass. A `filetype:pdf` query can still return landing pages; filename hints are not proof of PDF content. Register seed URLs and discovered navigation links are excluded from this search comparison.',
      '', 'The first batch encountered 71 temporary search-service failures. All were recovered by retrying at lower concurrency. Failures were recorded as errors, never as empty result sets. The PDF pass completed without unresolved service errors. Scharmühlenbach remained empty in both passes and two additional checks with the other search engine. This does not prove that sources or the watercourse do not exist.',
      '', '## What was run, in order','',
      '1. Read the uploaded code and recover all waterbodies from the supplied register. Prepare `sites_baseline.json` with regional qualifiers, keywords, existing seeds, historical context and identity caveats.',
      '2. Execute the unchanged collector to generate one overview request per site. Execute those external search requests, import actual results and retry service failures. Test the unchanged fetcher against three real endpoints, retaining any original response without extracting its contents.',
      '3. Inspect baseline weaknesses. Its normal document-processing path persists extracted text, so the baseline main run used a zero-fetch budget. This respected the earlier retention preference; no baseline fulltext collection was claimed.',
      '4. Implement originals-only processing, better search priorities, cautious identity rules and retry handling. Run regression tests, then perform a complete PDF-focused search pass and import it. Run bounded live collection batches.',
      '5. Inspect real results and refine again: preserve case-insensitive identity caveats, support river/locality term groups, handle hyphenated names, and prevent rejection after partial PDF reads. Reuse prior search evidence under the corrected profiles with explicit old/new ID mappings; this replay is not a new search.',
      '6. Rerun collection and cached-file screening. Verify original-file hashes, inspect metadata retention, export the per-site/source indexes and package the resumable dossier.',
      '', 'The acquisition budgets were 180, 180 and 240 seconds, followed by a cache-only validation pass. Time limits are checked between operations; in-flight work can extend a run slightly. The separate three-endpoint baseline fetch probe is retained for audit and excluded from final dossier totals.',
      '', '## What worked well','',
      '- The original collector already provided resumable SQLite queues, SHA-256 deduplication, robots handling, redirect checks, PDF body parsing, embedded-document discovery and bounded OCR support. Those capabilities were retained.',
      '- Searching PDF bodies finds regional reports even when a lake is absent from the document title. The script also follows metadata links, embedded files, opaque download endpoints and DOI links.',
      f'- In the retrieved corpus, **{counts["useful_pdfs_without_lake_in_embedded_title"]} distinct PDFs** matched relevance rules for at least one site despite lacking that site name in the embedded PDF title. An empty embedded title also qualifies here; this is not an assertion that every visible cover/title lacked the name. Audit rows are in `validation/nameless_pdf_examples.json`.',
      '- The second search pass increased the candidate inventory and the share of URLs with PDF filename hints. Search provenance and site associations remain traceable.',
      '', '## Changes made','',
      '| Problem | Implemented change |',
      '|---|---|',
      '| Extracted fulltext persisted by default | Originals-only default; transient text screening; metadata-only parse files and review records; no extracted text exports |',
      '| Broad overview searches dominate the first batch | Higher-priority PDF body searches, authority-domain routes, site research questions and shorter topic keyword queries |',
      '| Existing queued PDF queries keep old priorities | Upgrade pending query priority when reusing the same query ID |',
      '| Failed external searches treated as terminal | Bounded retry state and backoff, with errors distinct from genuine zero results |',
      '| Uncertain lake identity can be promoted | Explicit identity gate; corrected input warnings; optional geographic gate and nearby river/place term groups |',
      '| Partial reads can falsely exclude a source | Preserve an uncertain decision when a partial/failed parse lacks an identity match |',
      '| File extension is unreliable | Preserve byte-based detection and recognize a PDF header after leading bytes; keep opaque endpoint discovery |',
      '| Irrelevant search-form URLs consume crawl budget | Defer unrelated search-form results while retaining opaque document endpoints as candidates |',
      '| URL counts look like document counts | Export separate URL/SHA-256 group bases, original-byte status, relevance status and identity flags |',
      '| User must assemble a large technical config | `start_research.py` supplies IDs, 19 default topics and safe retention defaults from a short site/context input |',
      '', 'The code diff is in `validation/collector_changes.diff`. Inputs, actual search results and replay mappings are included.',
      '', '## Validation and output','',
      f'**{test_count} regression tests passed.** They cover later-page name matches in a generically titled PDF, extensionless PDF downloads, embedded links, partial parses, ambiguous identities, nearby river/place terms, privacy, legacy-dossier rejection, retries, deduplication, repeated runs and minimal-input startup. Synthetic fixtures are not included in research source counts. Live API-model review, paid search integration and OCR on real scanned files were not exercised.',
      '',f'All **{counts["integrity_verified_originals"]} archived originals** passed SHA-256 verification. The audit found **{counts["privacy_failures"]} retained fulltext/fragment violations** in parse metadata and review records; the dossier has no `extracted/` directory. Input context, titles, URLs, relevance labels and technical status remain. Temporary PDF-worker text is removed after use.',
      '', f'Archived originals are associated with **{counts["archived_sites"]} sites** so far. The other sites have search candidates or unresolved searches, not complete downloaded collections. A document can have several site associations and different relevance decisions; per-site totals must not be summed to obtain global unique files.',
      '', '- `SITE_OVERVIEW.csv`: all 137 entries, prepared context, keywords, identity flags and before/after retrieval counts.',
      '- `SOURCE_INDEX.csv` / `.jsonl`: candidate URL inventory with retrieval status, saved-file paths and site-specific decisions.',
      '- `dossier/archive/`: actual original files; `dossier/metadata/`: technical parse metadata.',
      '- `dossier/results/`: site manifests, search/discovery logs, access errors and current pending work.',
      '- `dossier/research.sqlite`: resumable queue and provenance; older semantic profiles remain as history.',
      '', '## Remaining limitations and useful next improvements','',
      '1. **Resolve the 39 identity warnings.** Add coordinates, an official waterbody ID or an explicitly scoped river segment before treating a matching name as a confirmed location. Keep ambiguous spellings as hypotheses until supported.',
      '2. **Continue original-file acquisition.** The current candidate inventory is much larger than the downloaded corpus. Repeat the supplied continuation command with bounded budgets. Some sources returned HTTP/network/robots errors or exceeded the 30 MB cap; these remain visible and were not bypassed.',
      '3. **Search authority document collections more deeply.** Execute the queued authority, alias, history and topic queries. Use management-plan annexes, regional lake compilations, catalogue records and cited DOI links to reach documents without lake names in their titles.',
      '4. **Add geographical evidence to the input.** For common names or small bays, a name plus generic topic words is only a preliminary filter. Nearby term groups help river reaches but do not replace coordinate-based checks.',
      '5. **Improve precision evaluation.** Manually label a representative sample of accepted, uncertain and rejected candidates before tuning weights. The present run measures retrieval and operational correctness, not scientific precision/recall against a gold standard.',
      '6. **Treat scanned and very long PDFs explicitly.** Enable bounded OCR where needed, increase page limits only for known collections and retain uncertain status when coverage remains partial. The run allowed up to 600 pages; that is still a limit.',
      '7. **Scale downloading carefully.** A future worker pool should retain one database writer and shared per-host limits/robots state. The current downloader remains serial; this run does not claim production-scale throughput.',
      '8. **Keep version grouping distinct from byte deduplication.** Equal hashes share one file. Alternate editions, scans and mirrors with different bytes are not yet proven to be one intellectual document group.',
      '', 'No scientific facts were imported into the SOLVE PostgreSQL or Neo4j systems. The result is a source collection and a reproducible, resumable search process—not a completed scientific synthesis.',
      '', '## Continue','',
      'Extract both delivered ZIPs into the same directory, then:',
      '', '```bash',
      'python -m pip install -r revised/requirements.txt',
      'python revised/run_web_enrichment.py run --config inputs/sites_final.json --workspace dossier',
      '```','',
      'For a new site, edit `revised/examples/arendsee.json` and run `revised/start_research.py` with `--input` and a new `--workspace`. Without a configured search API, search jobs are exported for external execution; they do not run automatically in standalone Python.', ''
    ]
    (ROOT/'MULTISITE_REPORT.md').write_text('\n'.join(lines))
    print(json.dumps(counts,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dossier',type=Path,required=True)
    run(p.parse_args().dossier)

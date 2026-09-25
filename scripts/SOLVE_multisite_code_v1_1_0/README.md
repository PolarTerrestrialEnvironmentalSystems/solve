# Blablador integration — local revision 1.6.1

1.6.1 adds safe response diagnostics and bounded smaller-batch retries for
malformed start-link reviews. Existing successful decisions remain reusable.

**Current setup:** the standard starter researches **Arendsee only**, using
**Serper instead of SearXNG**. Before downloading web search results, Blablador
selects up to 20 suitable entry URLs, excluding tourism/navigation and uncertain
candidates, and replenishes the pool through additional pages and queries within
the configured budgets. Keys are read from the user-specified GraphRAG `.env`
file; values are not persisted. Downloads, reviews and resume state are preserved.
Run `./Start-Blablador.cmd`. See [the current instructions](SERPER_ARENDSEE.md).

The standard starters now write new runs to `dossier_arendsee_serper_v1_6_1`.
The previous `dossier_blablador_arendsee_v1_4` remains available separately;
the shared download cache still permits reuse of existing originals.

The descriptions below document the earlier search providers and optional modes.

This copy adds the SearXNG search provider. The original baseline and historical
validation records remain unchanged and describe the earlier version.

The active Arendsee-only configuration enables checked search routes, reviewed
seed sources, Crossref and DataCite metadata discovery, Blablador pre-download prioritization
and evidence-based content review. It requires `BLABLADOR_KEY`. See the current
[German instructions](RECHERCHE_V1_4.md). Download reuse remains enabled.

Version 1.5.0 generates scientific portal searches for every configured lake,
with nine domains and public DataCite metadata discovery for datasets and
repositories. DataCite requires no additional key. Domain queries use SearXNG;
Crossref and DataCite also work independently of its routes. The standard starter
still covers Arendsee. Run `./Start-Blablador-Alle-Seen.cmd` for all 33 lakes using
`inputs/sites_blablador_33_scientific.json`; results go to
`dossier_blablador_alle_33_v1_5/results/`. Run budgets are shared across all lakes.
The offline suite has 111 passing tests; three live DataCite requests were checked
without downloading documents or invoking the LLM.

Version 1.4.2 added eight curated scientific entry points across IGB, UFZ,
PANGAEA, Springer Nature, Copernicus/HESS and PLOS One for Arendsee. The
top-level `scientific_sources` list supplies direct crawl tasks and additional
domain searches. Keeping it outside waterbody identity profiles preserves the
existing dossier's tasks and reviews. All entry points pass the existing
preflight/content checks. See [the source catalog](WISSENSCHAFTLICHE_QUELLEN.md).

From this project folder in PowerShell, run `./Start-Blablador.cmd`, or:

```powershell
# Environment and dependencies are already installed in this local copy.
.\.venv\Scripts\python.exe revised/run_web_enrichment.py run --config inputs/sites_blablador.json --workspace dossier_arendsee_serper_v1_6_1
```

This configuration temporarily covers only Arendsee, retaining all 19 topics.
The full selection of 33 waterbodies is preserved in
`inputs/sites_blablador_33_v1_4.json`. The extended Arendsee budgets allow at most
80 search requests, 120 fetch attempts, 1000 task passes and 3600 seconds per run.
Search health controls are separately capped at 12 calls. There are at most 200 LLM
preflight calls and 80 content reviews per run, including up to 20 unclear sources.
The planner can generate up to 40 queries on available providers and tries ten
informative searches without source gain before pausing that provider.
Crossref query variants and DataCite name variants remain available during SearXNG outages. With
`retrieval.wait_for_search_routes=true`, an idle run waits for the next search
retry within the run deadline. Cooldowns are preserved; Ctrl+C saves and stops.
`tasks` counts processing passes, including preflight/ranking; it is not a
download count. New summary fields show unique attempted tasks and remaining work.
An in-flight
operation can finish after the time budget. Repeat the command with the same
workspace to continue. It does not install a scheduled task.

For a single example waterbody:

```powershell
.\.venv\Scripts\python.exe revised/start_research.py --input revised/examples/arendsee.json --workspace dossier_arendsee
```

The starter chooses SearXNG unless BRAVE_API_KEY is set or the input explicitly
selects another provider. SearXNG itself needs no LLM key. The active Arendsee
configuration requires a key for both LLM stages. Generic example configurations
retain their earlier defaults.

SearXNG settings in `search`: `provider: "searxng"`,
`base_url: "https://search.blablador.fz-juelich.de"`, `language: "de"`,
`count: 20`, `timeout_seconds: 45`, `delay_seconds: 2`.
`count` caps the deduplicated hits from the first result page; it does not
promise that many results or request additional pages. The delay precedes each
search; it is a conservative local setting, not a published service quota.
HTTP 429 and transient network/server failures use bounded retries.

Engine failures are recorded as `search_engine_failures` events. Partial hits
are queued, but their coverage is incomplete. Empty results with engine failures
are retried, not marked as a successful zero-hit search. Failed engines are not
retried independently when partial hits exist. Queries with no failures and no
hits receive `no_results`. Search snippets are not retained. Originals and
provenance continue to be archived by the existing collector.

The active selection uses `dossier_arendsee_serper_v1_6_1` by default to keep prior
research separate. Previous input snapshots are in `inputs/history/` and
`validation/pre_retrieval_backup/`.
Use a new dossier for the first run. Old `waiting_external` jobs are not
converted automatically: provider-specific search IDs keep provenance distinct.
`inputs/sites_final.json` remains the original search-disabled configuration.

Run the offline regression suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s revised -p "test_*.py" -v
```

API reference: https://docs.searxng.org/dev/search_api.html

## Adaptive search and five-waterbody pilot

The historical pilot compared three strategies through `search.strategy`:

- `baseline`: the previous query planner and priority scheduler.
- `staged_greedy`: one overview search, one document search, then targeted
  missing-topic and authority searches, scored by observed source gain.
- `staged_epsilon`: the same stages with reproducible 20% exploration choices.

`adaptive_search.py` credits the first reviewed, rule-relevant SHA per waterbody
with 3 points and each newly covered topic with another 2 points. A matching name
and nearby topic are still only candidate evidence, not scientific verification.
Exact-byte mirrors count once. Similar documents with changed bytes can count
separately. The score uses a two-observation prior; this is a heuristic scheduler,
not UCB, Thompson sampling or a trained model. Different waterbodies can share
family feedback when run in one dossier. The comparison isolates waterbodies.

The planner waits for direct source checks (or three completed checks), keeps a
minimum overview/document stage, caps new query rows at `adaptive_max_queries`
(default 12), and pauses after `plateau_queries` (default 4) informative searches
without gain, with a minimum of four informative observations. Technical failures
and zero-yield partial-engine responses do not count as negative evidence.
Retries remain subject to the existing attempt/run budgets. Pausing is not proof
that no more sources exist; recorded engine failures require separate attention.

Identity matching now respects name boundaries, preserving the existing
Elbe-Umflutkanal spelling. Explicit `identity_exclusions` can mask named child
reservoirs; `ambiguous_place_name` conservatively handles town-only mentions.
Topic evidence must occur within a paragraph and 250 normalized characters of a
name (with a bounded fallback for identity groups). This does not resolve all
semantic ambiguity, tables or pronouns. All benchmark strategies use these same
updated rules, so the comparison is about scheduling, not about old/new reviewers.

Pilot configurations contain Arendsee, Bergwitzsee, Barleber See I, Barleber See II
and Rappbodetalsperre/Vorsperre Hassel. Run a strategy in its own NEW dossier:

```powershell
.\.venv\Scripts\python.exe revised/run_web_enrichment.py run --config inputs/pilot_5_staged_greedy.json --workspace dossier_pilot_greedy
```

The active Arendsee selection uses the separate `focused` strategy
described in RECHERCHE_V1_4.md. This does not declare a winner of the old pilot.
Use a new dossier when changing strategies: pending queries/history would mix policies.

Reproduce the interleaved live pilot in a new output directory:

```powershell
.\.venv\Scripts\python.exe revised/benchmark_search.py --output validation/pilot_new --rounds 4
```

The benchmark rotates strategies, allows four searches per waterbody and three
source attempts per search, and freezes first search responses and source bytes
(or access failures) in a shared cache. Budget-deferred links are not reviewed.
This is a bounded evaluation harness, not the unrestricted normal run loop.
Source requests remain sequential and obey robots/host delays via the common
fetcher. Different strategies share network snapshots but no review decisions.

Replay the captured pilot without network calls (once per output directory):

```powershell
.\.venv\Scripts\python.exe revised/benchmark_search.py --output validation/pilot_5_comparison --rounds 4 --replay-only
```

Replay checks reproducibility of decisions on these snapshots. Cached timing is
not a measurement of standalone speed. Live results are preliminary and require
manual source auditing; there is no labelled reference set for recall estimates.

---

# SOLVE multisite collector — tested revision

This package applies the uploaded `suche_script(1).zip` (version 1.0.0) to the
137 register entries other than Arendsee. `baseline/` preserves that upload;
`revised/` contains version 1.1.0. It is a derivative of this upload, not a
reconstruction of the separately discussed modular version 2.6.0.

See `MULTISITE_REPORT.md` for measured results, restrictions, and next steps.

## Start with only waterbody information and context

Python 3.10 or newer:

```bash
python -m pip install -r revised/requirements.txt
python revised/start_research.py --input revised/examples/arendsee.json --workspace my_dossier
```

Arendsee remains an example input. It was not a target of the multisite search.
Copy that example and change `name`, `region`, `context` and `keywords`.
You can put multiple entries in `waterbodies`. The starter generates IDs and
supplies the 19 topic groups from `common_topics.json`.

Optional fields include `aliases`, `seed_urls`, `preferred_domains`,
`research_questions`, and `identity_requires_confirmation`. For a river reach,
an explicit `identity_groups: [["river name", "locality"]]` can require both
terms near each other without treating the locality alone as a waterbody alias.
Tentative aliases are search hypotheses; they do not confirm identity.

If `BRAVE_API_KEY` is already set, the starter selects Brave for automatic
search. Otherwise it selects `searxng` and searches Blablador automatically.
Explicit `external` configurations still export search jobs. A standalone
Python process cannot call ChatGPT's built-in search tools by itself. External
jobs must be searched and imported, as was done in this run. No key is included.
The historical multisite configurations use `external`; the new
`inputs/sites_blablador.json` uses `searxng`.

## Continue the supplied dossier

After extracting the results archive next to this project folder:

```bash
python revised/run_web_enrichment.py run --config inputs/sites_final.json --workspace dossier
```

`sites_final.json` continues downloads and transient screening from the existing
queue; its search budget is zero so it does not create more external requests.
The run is bounded by time, task, host and size limits and can be repeated.
These are cooperative budgets: an in-flight fetch or parse may finish after
the overall time budget. Failed access remains visible in the manifests.

For more searches, raise `limits.max_searches_per_run` in a copy of the config
(for example 137). Search requests appear in
`dossier/results/search_requests.jsonl`. Use this response schema:

```json
{"search_id":"ID_FROM_REQUEST","status":"ok","results":[{"url":"https://example.org/document","title":"Source title"}]}
```

```bash
python revised/run_web_enrichment.py import-search --config inputs/sites_final.json --workspace dossier --responses responses.jsonl
python revised/run_web_enrichment.py run --config inputs/sites_final.json --workspace dossier
```

Return `status:"error"` for a failed search service call. Only an actually
completed search with no hits should have `status:"ok", results:[]`.
Keep external search calls at low concurrency; the first high-concurrency
batch encountered service errors and was successfully retried.

## What is retained

- Original PDF, HTML and plain-text response files under `archive/`, addressed
  by SHA-256. Equal bytes share one file, even when found through several URLs.
- Source URLs, titles, discovery links, search provenance, input context,
  technical parse status, identity warnings and preliminary relevance labels.
- `metadata/` contains no extracted fulltext, fragments or scientific facts.
- Text is processed temporarily to check relevance and find links/DOIs.
  PDF worker temporary files are deleted after processing. OCR is optional.
- Original files naturally retain their content. "No extracted text" does not
  mean that the original PDF or HTML is redacted.

`storage.persist_extracted_text` defaults to `false`; `review.mode` defaults to
`rules`. Asynchronous assistant review requires stored excerpts and therefore
requires an explicit text-retention opt-in. Synchronous API review is optional
and was not used or tested with live credentials here. In originals-only mode,
raw bibliography paragraphs and physical-access quotations are not exported;
DOI and URL discovery still works. Old dossiers containing extracted text are
rejected in this mode rather than silently described as text-free.

## Reading the outputs

`SITE_OVERVIEW.csv` gives one row per register entry. `SOURCE_INDEX.csv` gives
the deduplicated search/seed URL inventory and retrieval/review status.

`dossier/results/alle_quellen_inkl_verworfene.jsonl` also includes crawl-frontier
links, including deferred or irrelevant ones. Its row count is not a relevant
source count. `original_bytes=true` indicates a saved response; `decision` is
the separate, preliminary relevance result. An inaccessible candidate is not
counted as an archived file. PDF URL hints are not verified PDFs.

The per-site numbered manifests preserve compatibility with the uploaded
script. `02_seiteninhalte` now references archived web originals and metadata,
not extracted page text. Categories can overlap and must not be summed.
`group_basis=original_bytes` means a SHA-256 group; `unverified_url` means only
a URL candidate. The latter is not a confirmed document group.

## Reproduce the study

The prepared baseline and revised configs, actual search responses, retry
records, register export and code diff are included. Searches are not frozen
internet snapshots; reissuing a query later can return different results.

`prepare_inputs.py` reads `source_register/register.json` and builds configs.
The final preparation fixes case-insensitive identity warnings and supplies
river/place term groups:

```bash
python prepare_inputs.py --output sites_revised_prepared.json --refined
```

Changing semantic site context creates a new profile. Previously executed
search evidence can be explicitly remapped with `replay_search_metadata.py`;
it records both old and new IDs and does not execute or count new searches.
The final dossier includes this mapping. Old profiles remain as history;
current exports use only the profiles in `sites_final.json`.

## Tests

```bash
python -m pip install -r revised/requirements-test.txt
python -m unittest discover -s revised -p "test_*.py" -v
```

Synthetic fixtures test privacy, PDF body matching on later pages, opaque
download endpoints, partial reads, identity gates, retries, replay-safe query
priorities, deduplication and repeat runs. Fixtures are not counted as sources.
The Arendsee starter example was checked with `--plan-only`.

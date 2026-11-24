# Dateiverzeichnis der gebündelten Skripte

Automatisch aus den tatsächlich vorhandenen Dateien erstellt. Eingaberegister stehen separat unter `inputs/`. Keine Aussage zur Ausführbarkeit historischer Skripte; siehe [Skriptübersicht](SCRIPT_INDEX.md).

## scripts/seen_basis_pilot

| Datei | Beschreibung / Typ |
|---|---|
| [download.py](../scripts/seen_basis_pilot/download.py) | Bounded, resumable official-data pilot. Python >=3.10, standard library only. |
| [README.md](../scripts/seen_basis_pilot/README.md) | md |
| [start-linux.sh](../scripts/seen_basis_pilot/start-linux.sh) | sh |
| [test_download.py](../scripts/seen_basis_pilot/test_download.py) | py |
| [TESTBERICHT.md](../scripts/seen_basis_pilot/TESTBERICHT.md) | md |

## scripts/SOLVE_multisite_code_v1_1_0

| Datei | Beschreibung / Typ |
|---|---|
| [baseline/run_web_enrichment.py](../scripts/SOLVE_multisite_code_v1_1_0/baseline/run_web_enrichment.py) | SOLVE Webrecherche: resumable source harvesting, not scientific fact import. |
| [baseline/SOLVE_SCHLAGWORTLISTE_SEEN.md](../scripts/SOLVE_multisite_code_v1_1_0/baseline/SOLVE_SCHLAGWORTLISTE_SEEN.md) | md |
| [baseline/SOLVE_Webrecherche_Anleitung.md](../scripts/SOLVE_multisite_code_v1_1_0/baseline/SOLVE_Webrecherche_Anleitung.md) | md |
| [build_report.py](../scripts/SOLVE_multisite_code_v1_1_0/build_report.py) | Create the measured, reproducible multisite report from actual artifacts. |
| [ELBE_LINUX.md](../scripts/SOLVE_multisite_code_v1_1_0/ELBE_LINUX.md) | md |
| [ERWEITERTER_LAUF.md](../scripts/SOLVE_multisite_code_v1_1_0/ERWEITERTER_LAUF.md) | md |
| [LLM_UND_DOWNLOAD_CACHE.md](../scripts/SOLVE_multisite_code_v1_1_0/LLM_UND_DOWNLOAD_CACHE.md) | md |
| [PARALLEL_LINUX.md](../scripts/SOLVE_multisite_code_v1_1_0/PARALLEL_LINUX.md) | md |
| [prepare_inputs.py](../scripts/SOLVE_multisite_code_v1_1_0/prepare_inputs.py) | Build reproducible multi-site inputs from the supplied SOLVE register JSON. |
| [README.md](../scripts/SOLVE_multisite_code_v1_1_0/README.md) | md |
| [RECHERCHE_V1_4.md](../scripts/SOLVE_multisite_code_v1_1_0/RECHERCHE_V1_4.md) | md |
| [revised/adaptive_search.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/adaptive_search.py) | Staged search: bounded discovery, source feedback, greedy or epsilon exploration. |
| [revised/benchmark_search.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/benchmark_search.py) | Bounded, interleaved pilot with shared first-response snapshots. |
| [revised/build_elbe_inventory.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/build_elbe_inventory.py) | Reproduce the official WFD Elbe inventory and its spatial state selection. |
| [revised/check_blablador.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/check_blablador.py) | Check authentication/model listing before spawning a large crawler campaign. |
| [revised/configure_retrieval.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/configure_retrieval.py) | Reproducible, reviewed seed configuration for revision 1.4; no credentials. |
| [revised/credential_env.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/credential_env.py) | Load only the explicitly configured API keys; never execute a dotenv file. |
| [revised/elbe_campaign.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/elbe_campaign.py) | Prepare and resume source-only Elbe campaigns from reviewed seed manifests. |
| [revised/evidence_review.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/evidence_review.py) | Select short original source passages; models refer to IDs, never invent text. |
| [revised/focused_search.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/focused_search.py) | Fair, evidence-driven search stages; no learning claim or implicit URL-count reward. |
| [revised/human_review.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/human_review.py) | Human-review queue from existing evidence; no fetches or status changes. |
| [revised/llm_gate.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/llm_gate.py) | Cross-process LLM slots, request spacing and shared HTTP-429 backoff. |
| [revised/parallel_crawler.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/parallel_crawler.py) | Bounded process parallelism with one SQLite writer per persistent lake group. |
| [revised/prepare_elbe_seeds.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/prepare_elbe_seeds.py) | Prepare auditable lake-specific HTML/PDF seeds before the Linux crawl. |
| [revised/prepare_official_elbe_seeds.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/prepare_official_elbe_seeds.py) | Resolve exact official waterbody catalogue links and validate them, without search APIs. |
| [revised/prepare_pilot.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/prepare_pilot.py) | py |
| [revised/prepare_scientific_elbe_candidates.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/prepare_scientific_elbe_candidates.py) | Discover title-matched scientific candidates; never mark them accepted. |
| [revised/prime_download_cache.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/prime_download_cache.py) | Import already archived originals into the shared index without HTTP calls. |
| [revised/quality_search.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/quality_search.py) | Bounded search-route checks and metadata-only diagnostics; no search snippets. |
| [revised/replay_search_metadata.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/replay_search_metadata.py) | Reuse existing search evidence after an explicit context/profile revision. |
| [revised/report_pilot.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/report_pilot.py) | Generate the human-readable pilot report from recorded measurements. |
| [revised/requirements-inventory.txt](../scripts/SOLVE_multisite_code_v1_1_0/revised/requirements-inventory.txt) | txt |
| [revised/requirements-test.txt](../scripts/SOLVE_multisite_code_v1_1_0/revised/requirements-test.txt) | txt |
| [revised/requirements.txt](../scripts/SOLVE_multisite_code_v1_1_0/revised/requirements.txt) | txt |
| [revised/run_web_enrichment.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/run_web_enrichment.py) | SOLVE Webrecherche: resumable source harvesting, not scientific fact import. |
| [revised/scientific_discovery.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/scientific_discovery.py) | Waterbody-independent discovery of scientific publications and dataset pages. |
| [revised/serper_search.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/serper_search.py) | Serper organic search; credentials stay in the header, snippets stay transient. |
| [revised/shared_downloads.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/shared_downloads.py) | Shared successful URL -> original-byte cache. No fuzzy title-based equivalence. |
| [revised/source_preflight.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/source_preflight.py) | Metadata-only LLM screening before any source download. |
| [revised/start_links.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/start_links.py) | Select a bounded pool of useful Serper entry points before source downloads. |
| [revised/start_research.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/start_research.py) | One-command entry point from waterbody information and research context. |
| [revised/test_adaptive.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_adaptive.py) | py |
| [revised/test_api_resume.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_api_resume.py) | Coordinator retry tests with simulated processes and clock; no API requests. |
| [revised/test_check_blablador.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_check_blablador.py) | py |
| [revised/test_collector.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_collector.py) | Focused regression tests. Fixtures are synthetic and never research results. |
| [revised/test_credential_env.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_credential_env.py) | py |
| [revised/test_elbe_campaign.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_elbe_campaign.py) | py |
| [revised/test_elbe_inventory.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_elbe_inventory.py) | py |
| [revised/test_elbe_seeds.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_elbe_seeds.py) | Offline behavioral tests for bounded, resumable seed preparation. |
| [revised/test_extended_search.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_extended_search.py) | Provider outages and waiting are simulated; these are not research results. |
| [revised/test_human_review.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_human_review.py) | py |
| [revised/test_llm_gate.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_llm_gate.py) | py |
| [revised/test_official_elbe_seeds.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_official_elbe_seeds.py) | Exact catalogue mapping, HTTP content checks and resume; no live requests. |
| [revised/test_parallel_crawler.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_parallel_crawler.py) | py |
| [revised/test_preflight_cache.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_preflight_cache.py) | py |
| [revised/test_prepared_pdf_pages.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_prepared_pdf_pages.py) | Prepared official PDF page evidence: real synthetic PDFs, no live network. |
| [revised/test_prepared_seeds.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_prepared_seeds.py) | Prepared source lists and river identity: synthetic fixtures, no network calls. |
| [revised/test_retrieval.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_retrieval.py) | Behavioural regression tests for route health, retrieval and evidence gates. |
| [revised/test_scientific_discovery.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_scientific_discovery.py) | General scientific discovery; synthetic metadata, no external API calls. |
| [revised/test_scientific_elbe_candidates.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_scientific_elbe_candidates.py) | py |
| [revised/test_scientific_sources.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_scientific_sources.py) | Scientific entry points must preserve resume state and normal review gates. |
| [revised/test_search_disabled.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_search_disabled.py) | No-search mode preserves crawl/review work and reversibly pauses search queues. |
| [revised/test_searxng.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_searxng.py) | Offline adapter and queue regression tests for Blablador SearXNG. |
| [revised/test_serper.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_serper.py) | Offline adapter, provider migration, rate limits and credential-loading tests. |
| [revised/test_start_links.py](../scripts/SOLVE_multisite_code_v1_1_0/revised/test_start_links.py) | Synthetic entry-pool tests: no external calls or document downloads. |
| [SERPER_ARENDSEE.md](../scripts/SOLVE_multisite_code_v1_1_0/SERPER_ARENDSEE.md) | md |
| [setup-linux.sh](../scripts/SOLVE_multisite_code_v1_1_0/setup-linux.sh) | sh |
| [Start-33-Seen.cmd](../scripts/SOLVE_multisite_code_v1_1_0/Start-33-Seen.cmd) | cmd |
| [Start-33-Seen.ps1](../scripts/SOLVE_multisite_code_v1_1_0/Start-33-Seen.ps1) | ps1 |
| [Start-Blablador-Alle-Seen.cmd](../scripts/SOLVE_multisite_code_v1_1_0/Start-Blablador-Alle-Seen.cmd) | cmd |
| [Start-Blablador.cmd](../scripts/SOLVE_multisite_code_v1_1_0/Start-Blablador.cmd) | cmd |
| [Start-Blablador.ps1](../scripts/SOLVE_multisite_code_v1_1_0/Start-Blablador.ps1) | ps1 |
| [start-elbe-linux.sh](../scripts/SOLVE_multisite_code_v1_1_0/start-elbe-linux.sh) | sh |
| [start-parallel-linux.sh](../scripts/SOLVE_multisite_code_v1_1_0/start-parallel-linux.sh) | sh |
| [WISSENSCHAFTLICHE_QUELLEN.md](../scripts/SOLVE_multisite_code_v1_1_0/WISSENSCHAFTLICHE_QUELLEN.md) | md |

## scripts/wasserblick33

| Datei | Beschreibung / Typ |
|---|---|
| [common.py](../scripts/wasserblick33/common.py) | Small deterministic helpers; no database or network side effects on import. |
| [compose.yaml](../scripts/wasserblick33/compose.yaml) | yaml |
| [Dockerfile](../scripts/wasserblick33/Dockerfile) | Dockerfile |
| [download.py](../scripts/wasserblick33/download.py) | Download the selected official WISE WFD2022 report with full request provenance. |
| [export_audit.py](../scripts/wasserblick33/export_audit.py) | Human-readable joins and coverage reports; no new identity inference. |
| [init_env.py](../scripts/wasserblick33/init_env.py) | Create new local credentials. Existing files are never overwritten. |
| [INTEGRATION_TESTS.txt](../scripts/wasserblick33/INTEGRATION_TESTS.txt) | txt |
| [neo4j_import.py](../scripts/wasserblick33/neo4j_import.py) | Project validated PostgreSQL rows into an isolated, immutable Neo4j graph. |
| [pg_import.py](../scripts/wasserblick33/pg_import.py) | Import a verified version-1 bundle into a NEW immutable PostgreSQL schema. |
| [prepare.py](../scripts/wasserblick33/prepare.py) | Preserve source rows, normalize entities, and build auditable, conservative links. |
| [queries.cypher](../scripts/wasserblick33/queries.cypher) | cypher |
| [README.md](../scripts/wasserblick33/README.md) | md |
| [requirements-lock.txt](../scripts/wasserblick33/requirements-lock.txt) | txt |
| [requirements.txt](../scripts/wasserblick33/requirements.txt) | txt |
| [run_pipeline.py](../scripts/wasserblick33/run_pipeline.py) | Validate offline, or import PostgreSQL then the temporal Neo4j projection. |
| [schema.sql](../scripts/wasserblick33/schema.sql) | sql |
| [start.cmd](../scripts/wasserblick33/start.cmd) | cmd |
| [start.ps1](../scripts/wasserblick33/start.ps1) | ps1 |
| [start.sh](../scripts/wasserblick33/start.sh) | sh |
| [tests/test_database_integration.py](../scripts/wasserblick33/tests/test_database_integration.py) | Opt-in integration tests against an explicitly named isolated test database. |
| [tests/test_matching.py](../scripts/wasserblick33/tests/test_matching.py) | py |
| [tests/test_neo4j_import.py](../scripts/wasserblick33/tests/test_neo4j_import.py) | py |
| [tests/test_pg_import.py](../scripts/wasserblick33/tests/test_pg_import.py) | py |
| [tests/test_preparation.py](../scripts/wasserblick33/tests/test_preparation.py) | Offline preparation checks: input integrity, raw semantics and period-specific IDs. |
| [verify_delivery.py](../scripts/wasserblick33/verify_delivery.py) | Verify shipped data, source responses, event preservation and temporal graph. |

## scripts/bergwitzsee_v2

| Datei | Beschreibung / Typ |
|---|---|
| [build_postgres.py](../scripts/bergwitzsee_v2/build_postgres.py) | Load real Bergwitzsee sources into a NEW database with the original V2 DDL. |
| [event_extension.py](../scripts/bergwitzsee_v2/event_extension.py) | Select Bergwitzsee's source-preserving extension of the original V2 model. |
| [export_dump.py](../scripts/bergwitzsee_v2/export_dump.py) | Dump Bergwitzsee V2 + Events, perform a fresh offline restore, then restart its own runtime. |
| [export_postgres.py](../scripts/bergwitzsee_v2/export_postgres.py) | Back up the newly created Bergwitzsee PostGIS DB including V2 schema and event extensions. |
| [import_extensions.py](../scripts/bergwitzsee_v2/import_extensions.py) | Add Bergwitzsee's researched events and historical versions to original V2. |
| [import_graph.py](../scripts/bergwitzsee_v2/import_graph.py) | Import unchanged original V2 model, then add direct events and report versions. |
| [retain_lhw_master.py](../scripts/bergwitzsee_v2/retain_lhw_master.py) | Idempotent additive master-data preservation for the isolated Bergwitzsee build. |
| [setup_neo4j.py](../scripts/bergwitzsee_v2/setup_neo4j.py) | Initialize a separate, loopback-only Neo4j runtime for the Bergwitzsee V2 + Events graph. |
| [validate_delivery.py](../scripts/bergwitzsee_v2/validate_delivery.py) | Read-only, independent source-to-PostgreSQL-to-Neo4j delivery audit. |

## scripts/bergwitzsee_enrichment

| Datei | Beschreibung / Typ |
|---|---|
| [acquire_geodata.py](../scripts/bergwitzsee_enrichment/acquire_geodata.py) | Acquire bounded, source-preserving official geodata for Bergwitzsee. |
| [build_postgres_supplement.py](../scripts/bergwitzsee_enrichment/build_postgres_supplement.py) | Append verified official Bergwitzsee data to the isolated V2 clone. |
| [build_report_pdf.py](../scripts/bergwitzsee_enrichment/build_report_pdf.py) | Build the German, source-traceable Bergwitzsee report. No database access. |
| [clone_postgres.py](../scripts/bergwitzsee_enrichment/clone_postgres.py) | Create a NEW enrichment DB via pg_dump/pg_restore; leave all existing DBs intact. |
| [export_desktop_csv.py](../scripts/bergwitzsee_enrichment/export_desktop_csv.py) | Generate typed CSVs, exact baseline validator and atomic additive Desktop query. |
| [export_dump.py](../scripts/bergwitzsee_enrichment/export_dump.py) | Export the enriched Bergwitzsee graph and verify a complete isolated restore. |
| [export_postgres.py](../scripts/bergwitzsee_enrichment/export_postgres.py) | Back up the newly created Bergwitzsee PostGIS DB including V2 schema and event extensions. |
| [import_delta.py](../scripts/bergwitzsee_enrichment/import_delta.py) | Add only absent V2 nodes/edges from enriched PostgreSQL; never update old graph data. |
| [prepare_monitoring_supplement.py](../scripts/bergwitzsee_enrichment/prepare_monitoring_supplement.py) | Prepare source-faithful Bergwitzsee supplements from saved official sources. |
| [refresh_20261108.py](../scripts/bergwitzsee_enrichment/refresh_20261108.py) | Rebuild Bergwitzsee from preserved LHW exports and refreshed official supplements. |
| [setup_runtime.py](../scripts/bergwitzsee_enrichment/setup_runtime.py) | Initialize an isolated enrichment runtime seeded from the verified Bergwitzsee dump. |
| [snapshot_baseline.py](../scripts/bergwitzsee_enrichment/snapshot_baseline.py) | Read-only snapshot of every original graph node/property/relationship for additive validation. |
| [sync_censoring_metadata.py](../scripts/bergwitzsee_enrichment/sync_censoring_metadata.py) | Add corrected limit semantics to exactly the seven newly imported measurements. |
| [test_desktop_csv.py](../scripts/bergwitzsee_enrichment/test_desktop_csv.py) | Exercise Desktop CSV exact validator and importer, then roll back all test writes. |
| [validate_additive.py](../scripts/bergwitzsee_enrichment/validate_additive.py) | Verify every pre-existing graph entity/property survived the enrichment unchanged. |
| [validate_geodata.py](../scripts/bergwitzsee_enrichment/validate_geodata.py) | Independent integrity, identity and geometry audit of the acquired geodata. |
| [validate_kg_peer.py](../scripts/bergwitzsee_enrichment/validate_kg_peer.py) | Read-only independent review of PostgreSQL-to-Neo4j additions. |

## scripts/dummy_v2

| Datei | Beschreibung / Typ |
|---|---|
| [build_postgres.py](../scripts/dummy_v2/build_postgres.py) | Build the original Dummy V2 schema in a NEW task-owned database only. |
| [export_dump.py](../scripts/dummy_v2/export_dump.py) | Dump Dummy V2, perform a fresh offline restore, then restart its own runtime. |
| [export_postgres.py](../scripts/dummy_v2/export_postgres.py) | Back up the newly created Dummy PostGIS DB (including original polygon data). |
| [import_graph.py](../scripts/dummy_v2/import_graph.py) | Run the unchanged original V2 Neo4j importer against the isolated Dummy DB. |
| [requirements.txt](../scripts/dummy_v2/requirements.txt) | txt |
| [setup_neo4j.py](../scripts/dummy_v2/setup_neo4j.py) | Initialize a separate, loopback-only Neo4j runtime for the original Dummy V2 graph. |

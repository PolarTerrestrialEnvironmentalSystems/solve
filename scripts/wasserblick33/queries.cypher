// All queries select the active, validated import run explicitly. Older runs
// remain present and can be queried by replacing registry.active_run_id with
// their known run_id. Periods are reporting periods, not observation dates.

// 1. All available versions of Arendsee, keeping source retrieval time visible.
MATCH (registry:Solve33Registry {name:'wasserblick33'})
MATCH (lake:Lake33 {name:'Arendsee'})-[:REPRESENTS|PART_OF]->(v:WaterBodyVersion33)
WHERE lake.run_id=registry.active_run_id AND v.run_id=registry.active_run_id
RETURN lake.name, v.eu_code, v.reporting_cycle, v.report_year,
       v.period_start_year, v.period_end_year, v.retrieved_at, v.water_body_id
ORDER BY v.period_start_year, v.retrieved_at;

// 2. Compare ecological classifications through time for the SAME exact code.
// Unknown/raw classification values are not converted to numbers or zero.
MATCH (registry:Solve33Registry {name:'wasserblick33'})
MATCH (identity:ReportingUnit33 {eu_code:'DELW_DEST_MEL06OW17-00'})-[:HAS_VERSION]->(v:WaterBodyVersion33)
MATCH (a:Assessment33)-[:FOR_WATERBODY]->(v)
WHERE identity.run_id=registry.active_run_id
  AND a.parameter_code='swEcologicalStatusOrPotentialValue'
RETURN identity.eu_code, v.reporting_cycle, v.report_year,
       a.value_raw, a.value_raw_json, a.value_label, a.metadata_json, v.retrieved_at
ORDER BY v.period_start_year, v.retrieved_at;

// 3. All versions whose reporting period includes 2018. This is NOT a query
// saying a measurement occurred in 2018; actual assessment dates remain in
// Assessment33.metadata_json and event time/precision in Event33 properties.
MATCH (registry:Solve33Registry {name:'wasserblick33'})
MATCH (v:WaterBodyVersion33)
WHERE v.run_id=registry.active_run_id
  AND v.period_start_year <= 2018 AND v.period_end_year >= 2018
RETURN v.name, v.eu_code, v.reporting_cycle, v.report_year;

// 4. Predam/main-reservoir reporting identities across cycles. Three project
// Lake33 nodes remain distinct even when their current reporting body is shared.
MATCH (registry:Solve33Registry {name:'wasserblick33'})
MATCH (lake:Lake33)-[r:REPRESENTS|PART_OF]->(v:WaterBodyVersion33)
WHERE lake.run_id=registry.active_run_id AND lake.name STARTS WITH 'Rappbodetalsperre'
RETURN lake.name, type(r) AS relation, v.reporting_cycle, v.eu_code, v.category,
       r.evidence_json
ORDER BY lake.name, v.period_start_year;

// 5. Station versions remain separate; MONITORS uses the exact source FK.
MATCH (registry:Solve33Registry {name:'wasserblick33'})
MATCH (identity:MonitoringStationIdentity33)-[:HAS_VERSION]->(s:MonitoringStationVersion33)-[:MONITORS]->(v:WaterBodyVersion33)
WHERE identity.run_id=registry.active_run_id
RETURN identity.eu_code AS station_code, s.name, s.reporting_cycle,
       s.report_year, s.retrieved_at, v.eu_code AS reported_waterbody
ORDER BY station_code, s.period_start_year;

-- @SCHEMA@ is replaced only after strict identifier validation by pg_import.py.
-- These tables are created in a fresh immutable snapshot schema, never over an
-- existing prototype. Original records and event payloads remain available.
CREATE TABLE @SCHEMA@._import_run (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    bundle_sha256 text NOT NULL CHECK (bundle_sha256 ~ '^[0-9a-f]{64}$'),
    bundle_version integer NOT NULL CHECK (bundle_version = 1),
    imported_at timestamptz NOT NULL DEFAULT now(),
    counts jsonb NOT NULL CHECK (jsonb_typeof(counts) = 'object'),
    table_sha256 jsonb NOT NULL CHECK (jsonb_typeof(table_sha256) = 'object')
);
CREATE TABLE @SCHEMA@.dataset (
    dataset_id text PRIMARY KEY CHECK (length(dataset_id) > 0),
    reporting_cycle text,
    source_name text NOT NULL,
    source_url text NOT NULL,
    retrieved_at text NOT NULL,
    sha256 text CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
    metadata jsonb NOT NULL
);
CREATE TABLE @SCHEMA@.raw_record (
    raw_id text PRIMARY KEY CHECK (length(raw_id) > 0),
    dataset_id text NOT NULL REFERENCES @SCHEMA@.dataset(dataset_id),
    layer text NOT NULL,
    source_id text,
    payload jsonb NOT NULL,
    UNIQUE (raw_id, dataset_id)
);
CREATE TABLE @SCHEMA@.water_body (
    water_body_id text PRIMARY KEY CHECK (length(water_body_id) > 0),
    dataset_id text NOT NULL REFERENCES @SCHEMA@.dataset(dataset_id),
    eu_code text NOT NULL,
    name text NOT NULL,
    category text,
    geometry jsonb,
    raw_id text NOT NULL,
    attributes jsonb NOT NULL,
    geom geometry(Geometry,4326) GENERATED ALWAYS AS
      (CASE WHEN geometry IS NULL THEN NULL ELSE ST_SetSRID(ST_GeomFromGeoJSON(geometry),4326) END) STORED,
    FOREIGN KEY (raw_id, dataset_id) REFERENCES @SCHEMA@.raw_record(raw_id, dataset_id),
    UNIQUE (dataset_id, eu_code),
    CHECK (geom IS NULL OR (ST_IsValid(geom) AND ST_NDims(geom)=2 AND NOT ST_IsEmpty(geom))),
    CHECK (geom IS NULL OR (ST_XMin(Box3D(geom))>=-180 AND ST_XMax(Box3D(geom))<=180
      AND ST_YMin(Box3D(geom))>=-90 AND ST_YMax(Box3D(geom))<=90))
);
CREATE INDEX ON @SCHEMA@.water_body USING gist(geom);
CREATE TABLE @SCHEMA@.water_body_identifier (
    identifier_id text PRIMARY KEY,
    water_body_id text NOT NULL REFERENCES @SCHEMA@.water_body(water_body_id),
    scheme text NOT NULL,
    value text NOT NULL,
    provenance jsonb NOT NULL,
    UNIQUE (water_body_id, scheme, value)
);
CREATE INDEX ON @SCHEMA@.water_body_identifier(scheme,value);
CREATE TABLE @SCHEMA@.monitoring_station (
    station_id text PRIMARY KEY,
    dataset_id text NOT NULL REFERENCES @SCHEMA@.dataset(dataset_id),
    eu_code text,
    name text,
    geometry jsonb,
    raw_id text NOT NULL,
    attributes jsonb NOT NULL,
    geom geometry(Geometry,4326) GENERATED ALWAYS AS
      (CASE WHEN geometry IS NULL THEN NULL ELSE ST_SetSRID(ST_GeomFromGeoJSON(geometry),4326) END) STORED,
    FOREIGN KEY (raw_id, dataset_id) REFERENCES @SCHEMA@.raw_record(raw_id, dataset_id),
    CHECK (geom IS NULL OR (ST_IsValid(geom) AND ST_NDims(geom)=2 AND NOT ST_IsEmpty(geom))),
    CHECK (geom IS NULL OR (ST_XMin(Box3D(geom))>=-180 AND ST_XMax(Box3D(geom))<=180
      AND ST_YMin(Box3D(geom))>=-90 AND ST_YMax(Box3D(geom))<=90))
);
CREATE INDEX ON @SCHEMA@.monitoring_station USING gist(geom);
CREATE TABLE @SCHEMA@.station_water_body (
    link_id text PRIMARY KEY,
    station_id text NOT NULL REFERENCES @SCHEMA@.monitoring_station(station_id),
    water_body_id text NOT NULL REFERENCES @SCHEMA@.water_body(water_body_id),
    source_code text,
    method text NOT NULL,
    raw_id text REFERENCES @SCHEMA@.raw_record(raw_id)
);
CREATE TABLE @SCHEMA@.water_body_assessment (
    assessment_id text PRIMARY KEY,
    water_body_id text NOT NULL REFERENCES @SCHEMA@.water_body(water_body_id),
    parameter_code text NOT NULL,
    value_raw jsonb,
    value_label text,
    unit text,
    reporting_cycle text,
    raw_id text NOT NULL REFERENCES @SCHEMA@.raw_record(raw_id),
    metadata jsonb NOT NULL
);
CREATE TABLE @SCHEMA@.lake (
    lake_id text PRIMARY KEY,
    name text NOT NULL,
    aliases jsonb NOT NULL CHECK (jsonb_typeof(aliases) = 'array'),
    requested_identifiers jsonb NOT NULL CHECK (jsonb_typeof(requested_identifiers) = 'array')
);
CREATE TABLE @SCHEMA@.lake_water_body (
    link_id text PRIMARY KEY,
    lake_id text NOT NULL REFERENCES @SCHEMA@.lake(lake_id),
    water_body_id text NOT NULL REFERENCES @SCHEMA@.water_body(water_body_id),
    relation_type text NOT NULL CHECK (relation_type IN ('same_water_body','part_of')),
    method text NOT NULL,
    evidence jsonb NOT NULL,
    UNIQUE (lake_id, water_body_id)
);
CREATE TABLE @SCHEMA@.match_candidate (
    candidate_id text PRIMARY KEY,
    lake_id text NOT NULL REFERENCES @SCHEMA@.lake(lake_id),
    water_body_id text REFERENCES @SCHEMA@.water_body(water_body_id),
    status text NOT NULL CHECK (status IN ('accepted','review','unmatched','rejected')),
    method text NOT NULL,
    reason text NOT NULL,
    evidence jsonb NOT NULL,
    CHECK (status NOT IN ('accepted','review') OR water_body_id IS NOT NULL)
);
CREATE TABLE @SCHEMA@.source_document (
    source_id text PRIMARY KEY,
    url text NOT NULL,
    title text,
    metadata jsonb NOT NULL
);
CREATE TABLE @SCHEMA@.event (
    event_id text PRIMARY KEY,
    lake_id text NOT NULL REFERENCES @SCHEMA@.lake(lake_id),
    record_type text NOT NULL,
    statement text NOT NULL,
    time_start text,
    time_end text,
    time_precision text,
    payload jsonb NOT NULL
);
CREATE INDEX ON @SCHEMA@.event(lake_id);
CREATE TABLE @SCHEMA@.event_evidence (
    evidence_id text PRIMARY KEY,
    event_id text NOT NULL REFERENCES @SCHEMA@.event(event_id),
    source_id text NOT NULL REFERENCES @SCHEMA@.source_document(source_id),
    locator text NOT NULL,
    payload jsonb NOT NULL
);
CREATE INDEX ON @SCHEMA@.event_evidence(event_id);
CREATE TABLE @SCHEMA@.import_issue (
    issue_id text PRIMARY KEY,
    severity text NOT NULL CHECK (severity IN ('info','warning','error')),
    entity_type text NOT NULL,
    entity_id text,
    code text NOT NULL,
    message text NOT NULL,
    details jsonb NOT NULL
);

-- Temporal views preserve each reporting version as its own row. The exact
-- source EU code groups a reporting unit across versions; it does not establish
-- unchanged geometry, name, category, status or a continuous period of validity.
-- report_year is the source/report edition from dataset metadata, not the year
-- of a sample, event or download. retrieved_at is acquisition/system time only.
-- No version join uses a name, guessed alias or spatial nearest neighbour.
CREATE VIEW @SCHEMA@.water_body_history AS
SELECT w.water_body_id,w.eu_code,w.name,w.category,w.dataset_id,
       d.reporting_cycle,
       CASE WHEN d.metadata->>'report_year' ~ '^[0-9]{4}$'
            THEN (d.metadata->>'report_year')::integer ELSE NULL END AS report_year,
       d.retrieved_at,d.source_url,w.raw_id,w.geometry,w.geom,w.attributes
FROM @SCHEMA@.water_body w
JOIN @SCHEMA@.dataset d ON d.dataset_id=w.dataset_id;
COMMENT ON VIEW @SCHEMA@.water_body_history IS
 'One row per reported water-body version. Exact eu_code identifies a reporting unit, not necessarily an unchanged geographic extent. reporting_cycle/report_year are distinct from acquisition time retrieved_at; no validity interval is inferred.';

CREATE VIEW @SCHEMA@.station_history AS
SELECT s.station_id,s.eu_code,s.name,s.dataset_id,
       d.reporting_cycle,
       CASE WHEN d.metadata->>'report_year' ~ '^[0-9]{4}$'
            THEN (d.metadata->>'report_year')::integer ELSE NULL END AS report_year,
       d.retrieved_at,d.source_url,s.raw_id,s.geometry,s.geom,s.attributes
FROM @SCHEMA@.monitoring_station s
JOIN @SCHEMA@.dataset d ON d.dataset_id=s.dataset_id;
COMMENT ON VIEW @SCHEMA@.station_history IS
 'One row per reported station version, connected to its own dataset only. Compare exact source eu_code across report editions; a missing/changed code is not resolved by station name. Report time is not measurement time or acquisition time.';

-- Accepted lake-to-version assignments only. Missing/review assignments remain
-- in match_candidate/import_issue and are not silently converted to history.
CREATE VIEW @SCHEMA@.lake_reporting_history AS
SELECT l.lake_id,l.name AS lake_name,lw.link_id,lw.water_body_id,
       w.eu_code,w.name AS reported_water_body_name,w.category,w.dataset_id,
       w.reporting_cycle,w.report_year,w.retrieved_at,w.source_url,
       lw.relation_type,lw.method,lw.evidence
FROM @SCHEMA@.lake l
JOIN @SCHEMA@.lake_water_body lw ON lw.lake_id=l.lake_id
JOIN @SCHEMA@.water_body_history w ON w.water_body_id=lw.water_body_id;
COMMENT ON VIEW @SCHEMA@.lake_reporting_history IS
 'Verified contextual membership of project lakes in reported water-body versions. part_of remains distinct from same_water_body. Only accepted identifier-based links are included; absence is not proof of no historical water body.';

-- Contextual association only: joining on the lake does not prove that an
-- event caused a state/measurement, occurred in this reporting cycle, or belongs
-- to a particular monitoring site. Event time stays separate from report time.
CREATE VIEW @SCHEMA@.event_water_body_context AS
SELECT e.event_id,e.lake_id,l.name AS lake_name,lw.water_body_id,
       lw.relation_type,lw.method,lw.evidence,w.eu_code,w.dataset_id,
       w.reporting_cycle,w.report_year,w.retrieved_at,
       e.time_start AS event_time_start,e.time_end AS event_time_end,
       e.time_precision AS event_time_precision
FROM @SCHEMA@.event e
JOIN @SCHEMA@.lake l ON l.lake_id=e.lake_id
JOIN @SCHEMA@.lake_water_body lw ON lw.lake_id=e.lake_id
JOIN @SCHEMA@.water_body_history w ON w.water_body_id=lw.water_body_id;
COMMENT ON VIEW @SCHEMA@.event_water_body_context IS
 'Contextual event/lake/reported-water-body associations only. event_time_start/end are distinct from reporting_cycle/report_year and retrieved_at. This view does not assert temporal overlap or causation.';

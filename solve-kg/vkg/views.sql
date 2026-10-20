-- Schema fuer die aus PDFs extrahierten Fakten (passt zu den .obda-Mappings).
-- In das bestehende water_kg-Schema laden; Ontop bekommt nur read-only-Views.

CREATE SCHEMA IF NOT EXISTS water_kg;

-- ============ Rohtabellen (werden aus den CSVs geladen) ==================

CREATE TABLE IF NOT EXISTS water_kg.pdf_waterbody (
    wb_name     text,
    eu_cd_lw    text,          -- amtlicher Wasserkoerper-Code (nullable bis aufgeloest)
    n_events    integer,
    sources     text,
    map_x       double precision,
    map_y       double precision
);

CREATE TABLE IF NOT EXISTS water_kg.pdf_event (
    event_id        text PRIMARY KEY,
    eu_cd_lw        text,      -- Fremdschluessel auf den amtlichen Wasserkoerper (nullable)
    record_type     text,
    statement       text,
    ontology_concept text,
    time_original   text,
    source_document text,
    quote           text
);

CREATE TABLE IF NOT EXISTS water_kg.pdf_relationship (
    relationship_id   text PRIMARY KEY,
    source_event_id   text REFERENCES water_kg.pdf_event(event_id),
    target_event_id   text REFERENCES water_kg.pdf_event(event_id),
    relationship_type text,
    statement         text,
    mechanism         text,
    source_document   text
);

-- CSV laden (Pfade anpassen; CSVs liegen in diesem Ordner):
-- \copy water_kg.pdf_waterbody    FROM 'pdf_waterbody.csv'    WITH (FORMAT csv, HEADER true);
-- \copy water_kg.pdf_event        FROM 'pdf_event.csv'        WITH (FORMAT csv, HEADER true);
-- \copy water_kg.pdf_relationship FROM 'pdf_relationship.csv' WITH (FORMAT csv, HEADER true);

-- ============ Semantische View fuer Ontop (read-only) ====================
-- Hier kann spaeter eu_cd_lw auch per JOIN auf die amtliche WRRL-Liste
-- aufgeloest werden, statt aus pdf_event.eu_cd_lw zu kommen.

CREATE OR REPLACE VIEW water_kg.v_pdf_event_rdf AS
SELECT
    e.event_id,
    NULLIF(e.eu_cd_lw, '')       AS eu_cd_lw,
    e.record_type,
    e.statement,
    NULLIF(e.time_original, '')  AS time_original,
    e.source_document,
    NULLIF(e.quote, '')          AS quote
FROM water_kg.pdf_event e;

-- Leserechte fuer den Ontop-Nutzer:
-- GRANT USAGE ON SCHEMA water_kg TO ontop_reader;
-- GRANT SELECT ON water_kg.v_pdf_event_rdf, water_kg.pdf_relationship TO ontop_reader;

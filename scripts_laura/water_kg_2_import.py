"""Build the normalized ``water_kg_2`` schema from ``water_raw``.

The build happens in a temporary schema and is published only after validation.
The existing ``water_kg`` schema and Neo4j database are never modified.
"""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from sqlalchemy import create_engine, inspect, text


ROOT = Path(__file__).resolve().parent
RAW_SCHEMA = "water_raw"
TARGET_SCHEMA = "water_kg_2"
BUILD_SCHEMA = "water_kg_2_build"


def database_engine():
    password = os.getenv("SOLVE_DB_PASSWORD", "postgres")
    url = os.getenv(
        "SOLVE_DATABASE_URL",
        f"postgresql+psycopg://postgres:{password}@localhost:5432/SOLVE",
    )
    return create_engine(url, pool_pre_ping=True)


def clean_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    value = str(value).strip()
    return value or None


def key_text(value: Any) -> str | None:
    value = clean_text(value)
    if value is None:
        return None
    # CSV inference turns some otherwise textual station codes into floats.
    if value.endswith(".0"):
        try:
            return str(int(float(value)))
        except ValueError:
            pass
    return value


def number(value: Any) -> float | None:
    result = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(result) else float(result)


def integer(value: Any) -> int | None:
    result = number(value)
    return None if result is None else int(result)


def date_value(value: Any):
    result = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(result) else result.date()


def bool_code(value: Any) -> bool | None:
    value = clean_text(value)
    if value is None:
        return None
    normalized = value.casefold()
    if normalized in {"y", "yes", "ja", "true", "1"}:
        return True
    if normalized in {"n", "no", "nein", "false", "0"}:
        return False
    return None


def read_raw(engine, table: str, geo: bool = False):
    query = f'SELECT * FROM "{RAW_SCHEMA}"."{table}" ORDER BY source_row_number'
    if geo:
        return gpd.read_postgis(query, engine, geom_col="geometry")
    return pd.read_sql(query, engine)


def insert_frame(
    engine, schema: str, table: str, frame: pd.DataFrame,
    geo: bool = False, geometry_column: str = "geometry"
) -> None:
    if frame.empty:
        return
    if geo:
        gpd.GeoDataFrame(frame, geometry=geometry_column, crs=25833).to_postgis(
            table, engine, schema=schema, if_exists="append", index=False
        )
    else:
        frame.to_sql(
            table,
            engine,
            schema=schema,
            if_exists="append",
            index=False,
            chunksize=500,
            method="multi",
        )


def schema_ddl(schema: str) -> list[str]:
    return [
        "CREATE EXTENSION IF NOT EXISTS postgis",
        f'CREATE SCHEMA "{schema}"',
        f'''CREATE TABLE "{schema}".import_issue (
            issue_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            entity_type TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_row_number BIGINT NOT NULL,
            source_column TEXT NOT NULL,
            source_value TEXT,
            issue_code TEXT NOT NULL,
            message TEXT NOT NULL
        )''',
        f'''CREATE TABLE "{schema}".planning_unit (
            planning_unit_code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            participants TEXT,
            work_area_code TEXT,
            river_basin_code TEXT,
            country_state_code TEXT,
            delivery_date DATE,
            metadata_reference TEXT,
            url TEXT,
            wb_username TEXT,
            geometry geometry(Geometry,25833) NOT NULL
        )''',
        f'''CREATE TABLE "{schema}".water_body (
            water_body_id TEXT PRIMARY KEY,
            segment_code TEXT UNIQUE,
            name TEXT NOT NULL,
            lawa_id BIGINT,
            area_km2 DOUBLE PRECISION,
            volume_m3 DOUBLE PRECISION,
            volume_source TEXT,
            max_depth_m DOUBLE PRECISION,
            stratification TEXT,
            residence_time TEXT,
            lawa_type TEXT,
            water_type TEXT,
            artificial BOOLEAN,
            modified BOOLEAN,
            river_basin_code TEXT,
            source_planning_unit_code TEXT,
            planning_unit_code TEXT,
            ecological_status TEXT,
            chemical_status TEXT,
            geometry geometry(Geometry,25833) NOT NULL,
            CONSTRAINT fk_water_body_planning_unit FOREIGN KEY (planning_unit_code)
              REFERENCES "{schema}".planning_unit(planning_unit_code)
        )''',
        f'''CREATE TABLE "{schema}".water_body_identifier (
            identifier TEXT PRIMARY KEY,
            identifier_type TEXT NOT NULL,
            water_body_id TEXT NOT NULL,
            CONSTRAINT fk_water_identifier_body FOREIGN KEY (water_body_id)
              REFERENCES "{schema}".water_body(water_body_id)
        )''',
        f'''CREATE TABLE "{schema}".groundwater_body (
            groundwater_body_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            horizon INTEGER,
            aquifer_type TEXT,
            geological_formation TEXT,
            layered TEXT,
            river_basin_code TEXT,
            source_planning_unit_code TEXT,
            planning_unit_code TEXT,
            quantitative_status TEXT,
            chemical_status TEXT,
            metadata_reference TEXT,
            geometry geometry(Geometry,25833) NOT NULL,
            CONSTRAINT fk_groundwater_planning_unit FOREIGN KEY (planning_unit_code)
              REFERENCES "{schema}".planning_unit(planning_unit_code)
        )''',
        f'''CREATE TABLE "{schema}".catchment (
            catchment_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            area_km2 DOUBLE PRECISION,
            area_number TEXT,
            source_water_body_code TEXT,
            source_planning_unit_code TEXT,
            planning_unit_code TEXT,
            river_basin_code TEXT,
            water_authority TEXT,
            description_from TEXT,
            description_to TEXT,
            comment TEXT,
            metadata_reference TEXT,
            geometry geometry(Geometry,25833) NOT NULL,
            CONSTRAINT fk_catchment_planning_unit FOREIGN KEY (planning_unit_code)
              REFERENCES "{schema}".planning_unit(planning_unit_code)
        )''',
        f'''CREATE TABLE "{schema}".monitoring_station (
            station_id BIGINT PRIMARY KEY,
            water_body_id TEXT NOT NULL,
            eu_station_code TEXT,
            name TEXT,
            station_origin TEXT NOT NULL,
            location geometry(Geometry,25833),
            CONSTRAINT fk_station_water_body FOREIGN KEY (water_body_id)
              REFERENCES "{schema}".water_body(water_body_id),
            CONSTRAINT ck_station_origin CHECK (station_origin IN ('shapefile','observed_only'))
        )''',
        f'''CREATE UNIQUE INDEX uq_station_eu_code ON "{schema}".monitoring_station
            (water_body_id, eu_station_code) WHERE eu_station_code IS NOT NULL''',
        f'''CREATE TABLE "{schema}".station_alias (
            alias_id BIGINT PRIMARY KEY,
            station_id BIGINT NOT NULL,
            water_body_id TEXT NOT NULL,
            alias_code TEXT NOT NULL,
            alias_type TEXT NOT NULL,
            source_file TEXT NOT NULL,
            CONSTRAINT uq_station_alias UNIQUE (water_body_id, alias_code),
            CONSTRAINT fk_alias_station FOREIGN KEY (station_id)
              REFERENCES "{schema}".monitoring_station(station_id),
            CONSTRAINT fk_alias_water_body FOREIGN KEY (water_body_id)
              REFERENCES "{schema}".water_body(water_body_id),
            CONSTRAINT fk_alias_source FOREIGN KEY (source_file)
              REFERENCES "{RAW_SCHEMA}".source_file(source_file)
        )''',
        f'''CREATE TABLE "{schema}".core_location (
            core_code TEXT PRIMARY KEY,
            water_body_id TEXT NOT NULL,
            longitude DOUBLE PRECISION NOT NULL,
            latitude DOUBLE PRECISION NOT NULL,
            location geometry(Point,25833) NOT NULL,
            CONSTRAINT fk_core_water_body FOREIGN KEY (water_body_id)
              REFERENCES "{schema}".water_body(water_body_id)
        )''',
        f'''CREATE TABLE "{schema}".parameter (
            parameter_id BIGINT PRIMARY KEY,
            name TEXT NOT NULL,
            quality_component TEXT
        )''',
        f'''CREATE UNIQUE INDEX uq_parameter_identity ON "{schema}".parameter
            (name, COALESCE(quality_component,''))''',
        f'''CREATE TABLE "{schema}".unit (
            unit_id BIGINT PRIMARY KEY,
            symbol TEXT NOT NULL UNIQUE
        )''',
        f'''CREATE TABLE "{schema}".pollutant (
            pollutant_id BIGINT PRIMARY KEY,
            pollutant_code TEXT NOT NULL UNIQUE,
            pollutant_other TEXT
        )''',
        f'''CREATE TABLE "{schema}".paleo_element (
            element_id BIGINT PRIMARY KEY,
            symbol TEXT NOT NULL UNIQUE
        )''',
        f'''CREATE TABLE "{schema}".protected_area (
            protected_area_id TEXT PRIMARY KEY,
            name TEXT,
            area_type TEXT,
            legislation_code TEXT,
            member_state_code TEXT,
            status TEXT,
            water_protection_zone TEXT,
            area_status TEXT,
            river_basin_code TEXT,
            metadata_reference TEXT,
            url TEXT,
            source_file TEXT NOT NULL,
            geometry geometry(Geometry,25833) NOT NULL,
            CONSTRAINT fk_protected_source FOREIGN KEY (source_file)
              REFERENCES "{RAW_SCHEMA}".source_file(source_file)
        )''',
        f'''CREATE TABLE "{schema}".sampling_section (
            sampling_section_id BIGINT PRIMARY KEY,
            source_file TEXT NOT NULL,
            source_row_number BIGINT NOT NULL,
            water_body_id TEXT NOT NULL,
            station_id BIGINT,
            source_station_code TEXT NOT NULL,
            section_code TEXT NOT NULL,
            sampling_year INTEGER,
            length_from DOUBLE PRECISION,
            length_to DOUBLE PRECISION,
            depth_from DOUBLE PRECISION,
            depth_to DOUBLE PRECISION,
            CONSTRAINT uq_section_source UNIQUE(source_file,source_row_number),
            CONSTRAINT fk_section_source FOREIGN KEY(source_file)
              REFERENCES "{RAW_SCHEMA}".source_file(source_file),
            CONSTRAINT fk_section_water FOREIGN KEY(water_body_id)
              REFERENCES "{schema}".water_body(water_body_id),
            CONSTRAINT fk_section_station FOREIGN KEY(station_id)
              REFERENCES "{schema}".monitoring_station(station_id)
        )''',
        f'''CREATE TABLE "{schema}".measurement (
            measurement_id BIGINT PRIMARY KEY,
            source_file TEXT NOT NULL,
            source_row_number BIGINT NOT NULL,
            source_index BIGINT,
            water_body_id TEXT NOT NULL,
            station_id BIGINT,
            sampling_section_id BIGINT,
            source_station_code TEXT NOT NULL,
            source_section_code TEXT,
            parameter_id BIGINT NOT NULL,
            unit_id BIGINT,
            measured_at DATE,
            value DOUBLE PRECISION,
            comment TEXT,
            method TEXT,
            monitoring_program TEXT,
            abundance_type TEXT,
            CONSTRAINT uq_measurement_source UNIQUE(source_file,source_row_number),
            CONSTRAINT fk_measurement_source FOREIGN KEY(source_file)
              REFERENCES "{RAW_SCHEMA}".source_file(source_file),
            CONSTRAINT fk_measurement_water FOREIGN KEY(water_body_id)
              REFERENCES "{schema}".water_body(water_body_id),
            CONSTRAINT fk_measurement_station FOREIGN KEY(station_id)
              REFERENCES "{schema}".monitoring_station(station_id),
            CONSTRAINT fk_measurement_section FOREIGN KEY(sampling_section_id)
              REFERENCES "{schema}".sampling_section(sampling_section_id),
            CONSTRAINT fk_measurement_parameter FOREIGN KEY(parameter_id)
              REFERENCES "{schema}".parameter(parameter_id),
            CONSTRAINT fk_measurement_unit FOREIGN KEY(unit_id)
              REFERENCES "{schema}".unit(unit_id)
        )''',
        f'''CREATE TABLE "{schema}".water_body_assessment (
            assessment_id BIGINT PRIMARY KEY,
            source_file TEXT NOT NULL,
            source_row_number BIGINT NOT NULL,
            water_body_id TEXT NOT NULL,
            parameter_id BIGINT NOT NULL,
            assessment_year INTEGER,
            value_raw DOUBLE PRECISION,
            value_normalized DOUBLE PRECISION,
            is_valid BOOLEAN NOT NULL,
            validation_reason TEXT,
            method TEXT,
            CONSTRAINT uq_water_assessment_source UNIQUE(source_file,source_row_number),
            CONSTRAINT fk_water_assessment_source FOREIGN KEY(source_file)
              REFERENCES "{RAW_SCHEMA}".source_file(source_file),
            CONSTRAINT fk_water_assessment_body FOREIGN KEY(water_body_id)
              REFERENCES "{schema}".water_body(water_body_id),
            CONSTRAINT fk_water_assessment_parameter FOREIGN KEY(parameter_id)
              REFERENCES "{schema}".parameter(parameter_id)
        )''',
        f'''CREATE TABLE "{schema}".station_assessment (
            assessment_id BIGINT PRIMARY KEY,
            source_file TEXT NOT NULL,
            source_row_number BIGINT NOT NULL,
            water_body_id TEXT NOT NULL,
            station_id BIGINT,
            source_station_code TEXT NOT NULL,
            parameter_id BIGINT NOT NULL,
            unit_id BIGINT,
            assessment_year INTEGER,
            value_raw DOUBLE PRECISION,
            value_normalized DOUBLE PRECISION,
            is_valid BOOLEAN NOT NULL,
            validation_reason TEXT,
            description TEXT,
            method TEXT,
            expert_judgement TEXT,
            comment TEXT,
            reported_x DOUBLE PRECISION,
            reported_y DOUBLE PRECISION,
            CONSTRAINT uq_station_assessment_source UNIQUE(source_file,source_row_number),
            CONSTRAINT fk_station_assessment_source FOREIGN KEY(source_file)
              REFERENCES "{RAW_SCHEMA}".source_file(source_file),
            CONSTRAINT fk_station_assessment_water FOREIGN KEY(water_body_id)
              REFERENCES "{schema}".water_body(water_body_id),
            CONSTRAINT fk_station_assessment_station FOREIGN KEY(station_id)
              REFERENCES "{schema}".monitoring_station(station_id),
            CONSTRAINT fk_station_assessment_parameter FOREIGN KEY(parameter_id)
              REFERENCES "{schema}".parameter(parameter_id),
            CONSTRAINT fk_station_assessment_unit FOREIGN KEY(unit_id)
              REFERENCES "{schema}".unit(unit_id)
        )''',
        f'''CREATE TABLE "{schema}".chemical_status (
            chemical_status_id BIGINT PRIMARY KEY,
            source_file TEXT NOT NULL,
            source_row_number BIGINT NOT NULL,
            surface_water_body_id TEXT,
            groundwater_body_id TEXT,
            pollutant_id BIGINT NOT NULL,
            failed_standard BOOLEAN,
            risk_code TEXT,
            upward_trend_code TEXT,
            trend_code TEXT,
            trend_reversal_code TEXT,
            exemption_type TEXT,
            exemption_reason TEXT,
            exception_code TEXT,
            level_set TEXT,
            level_value DOUBLE PRECISION,
            level_unit TEXT,
            fail_reason_code TEXT,
            fail_reason_other TEXT,
            inserted_at DATE,
            inserted_by TEXT,
            river_basin_code TEXT,
            metadata_reference TEXT,
            CONSTRAINT uq_chemical_source UNIQUE(source_file,source_row_number),
            CONSTRAINT ck_chemical_owner CHECK (
              (surface_water_body_id IS NOT NULL)::integer +
              (groundwater_body_id IS NOT NULL)::integer = 1),
            CONSTRAINT fk_chemical_source FOREIGN KEY(source_file)
              REFERENCES "{RAW_SCHEMA}".source_file(source_file),
            CONSTRAINT fk_chemical_surface FOREIGN KEY(surface_water_body_id)
              REFERENCES "{schema}".water_body(water_body_id),
            CONSTRAINT fk_chemical_ground FOREIGN KEY(groundwater_body_id)
              REFERENCES "{schema}".groundwater_body(groundwater_body_id),
            CONSTRAINT fk_chemical_pollutant FOREIGN KEY(pollutant_id)
              REFERENCES "{schema}".pollutant(pollutant_id)
        )''',
        f'''CREATE TABLE "{schema}".paleo_measurement (
            paleo_measurement_id BIGINT PRIMARY KEY,
            source_file TEXT NOT NULL,
            source_row_number BIGINT NOT NULL,
            core_code TEXT NOT NULL,
            element_id BIGINT NOT NULL,
            age_years_bp INTEGER NOT NULL,
            value_dimensionless DOUBLE PRECISION NOT NULL,
            CONSTRAINT uq_paleo_source UNIQUE(source_file,source_row_number),
            CONSTRAINT fk_paleo_source FOREIGN KEY(source_file)
              REFERENCES "{RAW_SCHEMA}".source_file(source_file),
            CONSTRAINT fk_paleo_core FOREIGN KEY(core_code)
              REFERENCES "{schema}".core_location(core_code),
            CONSTRAINT fk_paleo_element FOREIGN KEY(element_id)
              REFERENCES "{schema}".paleo_element(element_id)
        )''',
        f'''CREATE TABLE "{schema}".protected_area_assessment (
            assessment_id BIGINT PRIMARY KEY,
            source_file TEXT NOT NULL,
            source_row_number BIGINT NOT NULL,
            source_protected_area_code TEXT NOT NULL,
            source_water_body_code TEXT NOT NULL,
            protected_area_id TEXT,
            surface_water_body_id TEXT,
            groundwater_body_id TEXT,
            template TEXT,
            name TEXT,
            legislation_code TEXT,
            legislation_link TEXT,
            area_type TEXT,
            designation_begin DATE,
            evolution_type TEXT,
            comment TEXT,
            exemption_code TEXT,
            habitat_status TEXT,
            metadata_reference TEXT,
            CONSTRAINT uq_protected_assessment_source UNIQUE(source_file,source_row_number),
            CONSTRAINT fk_protected_assessment_source FOREIGN KEY(source_file)
              REFERENCES "{RAW_SCHEMA}".source_file(source_file),
            CONSTRAINT fk_protected_assessment_area FOREIGN KEY(protected_area_id)
              REFERENCES "{schema}".protected_area(protected_area_id),
            CONSTRAINT fk_protected_assessment_surface FOREIGN KEY(surface_water_body_id)
              REFERENCES "{schema}".water_body(water_body_id),
            CONSTRAINT fk_protected_assessment_ground FOREIGN KEY(groundwater_body_id)
              REFERENCES "{schema}".groundwater_body(groundwater_body_id)
        )''',
        f'''CREATE TABLE "{schema}".water_body_groundwater_body (
            water_body_id TEXT NOT NULL,
            groundwater_body_id TEXT NOT NULL,
            overlap_area_km2 DOUBLE PRECISION NOT NULL,
            water_body_overlap_ratio DOUBLE PRECISION,
            determination_method TEXT NOT NULL,
            PRIMARY KEY(water_body_id,groundwater_body_id),
            FOREIGN KEY(water_body_id) REFERENCES "{schema}".water_body(water_body_id),
            FOREIGN KEY(groundwater_body_id) REFERENCES "{schema}".groundwater_body(groundwater_body_id)
        )''',
        f'''CREATE TABLE "{schema}".catchment_groundwater_body (
            catchment_id TEXT NOT NULL,
            groundwater_body_id TEXT NOT NULL,
            overlap_area_km2 DOUBLE PRECISION NOT NULL,
            catchment_overlap_ratio DOUBLE PRECISION,
            determination_method TEXT NOT NULL,
            PRIMARY KEY(catchment_id,groundwater_body_id),
            FOREIGN KEY(catchment_id) REFERENCES "{schema}".catchment(catchment_id),
            FOREIGN KEY(groundwater_body_id) REFERENCES "{schema}".groundwater_body(groundwater_body_id)
        )''',
        f'''CREATE TABLE "{schema}".catchment_water_body (
            catchment_id TEXT NOT NULL,
            water_body_id TEXT NOT NULL,
            overlap_area_km2 DOUBLE PRECISION NOT NULL,
            catchment_overlap_ratio DOUBLE PRECISION,
            water_body_overlap_ratio DOUBLE PRECISION,
            determination_method TEXT NOT NULL,
            PRIMARY KEY(catchment_id,water_body_id),
            FOREIGN KEY(catchment_id) REFERENCES "{schema}".catchment(catchment_id),
            FOREIGN KEY(water_body_id) REFERENCES "{schema}".water_body(water_body_id)
        )''',
        f'''CREATE TABLE "{schema}".station_groundwater_body (
            station_id BIGINT NOT NULL,
            groundwater_body_id TEXT NOT NULL,
            determination_method TEXT NOT NULL,
            PRIMARY KEY(station_id,groundwater_body_id),
            FOREIGN KEY(station_id) REFERENCES "{schema}".monitoring_station(station_id),
            FOREIGN KEY(groundwater_body_id) REFERENCES "{schema}".groundwater_body(groundwater_body_id)
        )''',
        f'''CREATE TABLE "{schema}".station_catchment (
            station_id BIGINT NOT NULL,
            catchment_id TEXT NOT NULL,
            determination_method TEXT NOT NULL,
            PRIMARY KEY(station_id,catchment_id),
            FOREIGN KEY(station_id) REFERENCES "{schema}".monitoring_station(station_id),
            FOREIGN KEY(catchment_id) REFERENCES "{schema}".catchment(catchment_id)
        )''',
        f'''CREATE TABLE "{schema}".water_body_protected_area (
            water_body_id TEXT NOT NULL,
            protected_area_id TEXT NOT NULL,
            relation_method TEXT NOT NULL,
            overlap_area_km2 DOUBLE PRECISION,
            water_body_overlap_ratio DOUBLE PRECISION,
            PRIMARY KEY(water_body_id,protected_area_id,relation_method),
            FOREIGN KEY(water_body_id) REFERENCES "{schema}".water_body(water_body_id),
            FOREIGN KEY(protected_area_id) REFERENCES "{schema}".protected_area(protected_area_id)
        )''',
    ]


def create_schema(engine, schema: str) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        for statement in schema_ddl(schema):
            connection.exec_driver_sql(statement)


def issue(issues: list[dict[str, Any]], entity: str, source_file: str, row: int,
          column: str, value: Any, code: str, message: str) -> None:
    issues.append({
        "entity_type": entity,
        "source_file": source_file,
        "source_row_number": int(row),
        "source_column": column,
        "source_value": clean_text(value),
        "issue_code": code,
        "message": message,
    })


def build_schema(engine, schema: str) -> dict[str, int]:
    issues: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    plan_raw = read_raw(engine, "planunits", geo=True)
    planning_units = gpd.GeoDataFrame({
        "planning_unit_code": plan_raw.planu_cd.map(key_text),
        "name": plan_raw.planu_name.map(clean_text),
        "participants": plan_raw.parti.map(clean_text),
        "work_area_code": plan_raw.wa_cd.map(key_text),
        "river_basin_code": plan_raw.rbd_cd.map(key_text),
        "country_state_code": plan_raw.land_cd.map(key_text),
        "delivery_date": plan_raw.delivery.map(date_value),
        "metadata_reference": plan_raw.metadata.map(clean_text),
        "url": plan_raw.url.map(clean_text),
        "wb_username": plan_raw.wbusername.map(clean_text),
        "geometry": plan_raw.geometry,
    }, geometry="geometry", crs=25833)
    insert_frame(engine, schema, "planning_unit", planning_units, geo=True)
    counts["planning_unit"] = len(planning_units)
    valid_plans = set(planning_units.planning_unit_code)

    def resolve_plan(code: Any) -> str | None:
        code = key_text(code)
        return code if code in valid_plans else None

    lake_raw = read_raw(engine, "lakewaterbody", geo=True)
    base_raw = read_raw(engine, "stammdaten")
    merged = lake_raw.merge(base_raw, on="eu_cd_lw", how="left", suffixes=("", "_base"))
    water_bodies = gpd.GeoDataFrame({
        "water_body_id": merged.eu_cd_lw.map(key_text),
        "segment_code": merged.eu_cd_ls.map(key_text),
        "name": merged.s_name.map(clean_text),
        "lawa_id": merged.lawa_id.map(integer),
        "area_km2": merged.flaeche_atkis.map(number).map(lambda x: None if x is None else x / 100.0),
        "volume_m3": merged.volumen.map(number),
        "volume_source": merged.volumen_quelle.map(clean_text),
        "max_depth_m": merged.z_max.map(number),
        "stratification": merged.schichtung.map(clean_text),
        "residence_time": merged.verweilzeit.map(clean_text),
        "lawa_type": merged.lawa_ti_typ.map(clean_text),
        "water_type": merged.typ_2021.map(key_text),
        "artificial": merged.artificial_2021.map(bool_code),
        "modified": merged.modified_2021.map(bool_code),
        "river_basin_code": merged.rbd_cd.map(key_text),
        "source_planning_unit_code": merged.planu_cd.map(key_text),
        "planning_unit_code": merged.planu_cd.map(resolve_plan),
        "ecological_status": merged.eco_stat.map(clean_text),
        "chemical_status": merged.chem_stat.map(clean_text),
        "geometry": merged.geometry,
    }, geometry="geometry", crs=25833)
    insert_frame(engine, schema, "water_body", water_bodies, geo=True)
    counts["water_body"] = len(water_bodies)
    water_ids = set(water_bodies.water_body_id)
    identifiers = []
    for row in water_bodies.itertuples(index=False):
        identifiers.append({"identifier": row.water_body_id, "identifier_type": "EU_CD_LW", "water_body_id": row.water_body_id})
        if row.segment_code and row.segment_code != row.water_body_id:
            identifiers.append({"identifier": row.segment_code, "identifier_type": "EU_CD_LS", "water_body_id": row.water_body_id})
    insert_frame(engine, schema, "water_body_identifier", pd.DataFrame(identifiers))

    ground_raw = read_raw(engine, "groundwaterbody", geo=True)
    groundwater = gpd.GeoDataFrame({
        "groundwater_body_id": ground_raw.eu_cd_gb.map(key_text),
        "name": ground_raw.nametext.map(clean_text),
        "horizon": ground_raw.horizon.map(integer),
        "aquifer_type": ground_raw.aqui_type.map(clean_text),
        "geological_formation": ground_raw.geol_form.map(clean_text),
        "layered": ground_raw.layered.map(clean_text),
        "river_basin_code": ground_raw.rbd_cd.map(key_text),
        "source_planning_unit_code": ground_raw.planu_cd.map(key_text),
        "planning_unit_code": ground_raw.planu_cd.map(resolve_plan),
        "quantitative_status": ground_raw.quant_stat.map(clean_text),
        "chemical_status": ground_raw.chem_stat.map(clean_text),
        "metadata_reference": ground_raw.metadata.map(clean_text),
        "geometry": ground_raw.geometry,
    }, geometry="geometry", crs=25833)
    for row in ground_raw.itertuples(index=False):
        code = key_text(row.planu_cd)
        if code and code not in valid_plans:
            issue(issues, "groundwater_body", "groundwaterbody.shp", row.source_row_number,
                  "PLANU_CD", code, "unresolved_planning_unit", "Planungseinheit fehlt in planunits.shp")
    insert_frame(engine, schema, "groundwater_body", groundwater, geo=True)
    counts["groundwater_body"] = len(groundwater)
    groundwater_ids = set(groundwater.groundwater_body_id)

    catch_raw = read_raw(engine, "catchments", geo=True)
    catchments = gpd.GeoDataFrame({
        "catchment_id": catch_raw.drain_c.map(key_text),
        "name": catch_raw.drain_n.map(clean_text),
        "area_km2": catch_raw.area_ca.map(number),
        "area_number": catch_raw.area_no.map(key_text),
        "source_water_body_code": catch_raw.eu_cd_w.map(key_text),
        "source_planning_unit_code": catch_raw.planu_c.map(key_text),
        "planning_unit_code": catch_raw.planu_c.map(resolve_plan),
        "river_basin_code": catch_raw.rbd_cd.map(key_text),
        "water_authority": catch_raw.wa_cd.map(key_text),
        "description_from": catch_raw.descr_f.map(clean_text),
        "description_to": catch_raw.descr_t.map(clean_text),
        "comment": catch_raw.comment.map(clean_text),
        "metadata_reference": catch_raw.metadata.map(clean_text),
        "geometry": catch_raw.geometry,
    }, geometry="geometry", crs=25833)
    insert_frame(engine, schema, "catchment", catchments, geo=True)
    counts["catchment"] = len(catchments)

    # Build known stations, then create observed-only stations for every missing
    # (water body, local station code) pair from measurements and assessments.
    station_raw = read_raw(engine, "messstellen", geo=True)
    station_rows: list[dict[str, Any]] = []
    alias_candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for row in station_raw.itertuples(index=False):
        water_id = key_text(row.eu_cd_wb)
        station_id = len(station_rows) + 1
        station_rows.append({
            "station_id": station_id, "water_body_id": water_id,
            "eu_station_code": key_text(row.eu_cd_sm), "name": clean_text(row.name_stn),
            "station_origin": "shapefile", "geometry": row.geometry,
        })
        for code, alias_type in ((row.eu_cd_sm, "eu_code"), (row.name_stn, "local_code")):
            code = key_text(code)
            if code:
                alias_candidates[(water_id, code)] = {
                    "station_id": station_id, "water_body_id": water_id,
                    "alias_code": code, "alias_type": alias_type,
                    "source_file": "messstellen.shp",
                }

    observed_sources = [
        ("einzeldaten.csv", read_raw(engine, "einzeldaten"), "messstelle", "eu_cd_lw"),
        ("einzeldaten_chemie.csv", read_raw(engine, "einzeldaten_chemie"), "messstelle", "eu_cd_lw"),
        ("einzeldaten_abschnitte.csv", read_raw(engine, "einzeldaten_abschnitte"), "messstelle", "eu_cd_lw"),
        ("messstellen_bewertung.csv", read_raw(engine, "messstellen_bewertung"), "messstelle", "eu_cd_lw"),
    ]
    assessment_coordinates: dict[tuple[str, str], Point] = {}
    assessment_raw = observed_sources[-1][1]
    for row in assessment_raw.itertuples(index=False):
        water_id, code = key_text(row.eu_cd_lw), key_text(row.messstelle)
        x, y = number(row.x_wert), number(row.y_wert)
        if water_id and code and x is not None and y is not None:
            assessment_coordinates.setdefault((water_id, code), Point(x, y))

    for source_file, frame, station_column, water_column in observed_sources:
        for row in frame.itertuples(index=False):
            water_id = key_text(getattr(row, water_column))
            code = key_text(getattr(row, station_column))
            if not water_id or not code or water_id not in water_ids:
                continue
            pair = (water_id, code)
            if pair not in alias_candidates:
                station_id = len(station_rows) + 1
                station_rows.append({
                    "station_id": station_id, "water_body_id": water_id,
                    "eu_station_code": None, "name": code,
                    "station_origin": "observed_only",
                    "geometry": assessment_coordinates.get(pair),
                })
                alias_candidates[pair] = {
                    "station_id": station_id, "water_body_id": water_id,
                    "alias_code": code, "alias_type": "observed_code",
                    "source_file": source_file,
                }
    stations = gpd.GeoDataFrame(station_rows, geometry="geometry", crs=25833).rename_geometry("location")
    insert_frame(engine, schema, "monitoring_station", stations, geo=True, geometry_column="location")
    counts["monitoring_station"] = len(stations)
    aliases = pd.DataFrame([
        {"alias_id": index, **row}
        for index, row in enumerate(alias_candidates.values(), start=1)
    ])
    insert_frame(engine, schema, "station_alias", aliases)
    counts["station_alias"] = len(aliases)
    station_lookup = {pair: row["station_id"] for pair, row in alias_candidates.items()}

    core_raw = read_raw(engine, "core_locations")
    core = gpd.GeoDataFrame({
        "core_code": core_raw.core_code.map(key_text),
        "water_body_id": core_raw.eu_cd_lw.map(key_text),
        "longitude": core_raw.x_coord.map(number),
        "latitude": core_raw.y_coord.map(number),
    }, geometry=[Point(x, y) for x, y in zip(core_raw.x_coord, core_raw.y_coord)], crs=4326).to_crs(25833)
    core = core.rename_geometry("location")
    insert_frame(engine, schema, "core_location", core, geo=True, geometry_column="location")
    counts["core_location"] = len(core)

    bio_raw, chem_raw = observed_sources[0][1], observed_sources[1][1]
    body_assessment_raw = read_raw(engine, "wasserkoerper_bewertung")
    parameter_sources = [
        pd.DataFrame({"name": bio_raw.parameter, "component": bio_raw.qk}),
        pd.DataFrame({"name": chem_raw.parameterbezeichnung, "component": chem_raw.qk}),
        pd.DataFrame({"name": body_assessment_raw.parameter, "component": body_assessment_raw.qk}),
        pd.DataFrame({"name": assessment_raw.parameter, "component": assessment_raw.komponente}),
    ]
    parameter_keys = sorted({
        (clean_text(row.name), clean_text(row.component))
        for frame in parameter_sources for row in frame.itertuples(index=False)
        if clean_text(row.name)
    }, key=lambda item: (item[0].casefold(), (item[1] or "").casefold()))
    parameters = pd.DataFrame([
        {"parameter_id": index, "name": name, "quality_component": component}
        for index, (name, component) in enumerate(parameter_keys, start=1)
    ])
    insert_frame(engine, schema, "parameter", parameters)
    counts["parameter"] = len(parameters)
    parameter_lookup = {
        (clean_text(row.name), clean_text(row.quality_component)): row.parameter_id
        for row in parameters.itertuples(index=False)
    }

    unit_values = sorted({
        clean_text(value) for frame, column in ((bio_raw, "einheit"), (chem_raw, "einheit"), (assessment_raw, "einheit"))
        for value in frame[column] if clean_text(value)
    }, key=str.casefold)
    units = pd.DataFrame([{"unit_id": i, "symbol": value} for i, value in enumerate(unit_values, 1)])
    insert_frame(engine, schema, "unit", units)
    unit_lookup = {row.symbol: row.unit_id for row in units.itertuples(index=False)}
    counts["unit"] = len(units)

    gw_status_raw, ow_status_raw = read_raw(engine, "gw_measures"), read_raw(engine, "ow_measures")
    pollutant_other: dict[str, str | None] = {}
    for row in gw_status_raw.itertuples(index=False):
        pollutant_other.setdefault(key_text(row.pollcode), clean_text(row.pollother))
    for value in ow_status_raw.pscode:
        pollutant_other.setdefault(key_text(value), None)
    pollutants = pd.DataFrame([
        {"pollutant_id": i, "pollutant_code": code, "pollutant_other": pollutant_other[code]}
        for i, code in enumerate(sorted(pollutant_other), 1)
    ])
    insert_frame(engine, schema, "pollutant", pollutants)
    pollutant_lookup = {row.pollutant_code: row.pollutant_id for row in pollutants.itertuples(index=False)}
    counts["pollutant"] = len(pollutants)

    paleo_raw = read_raw(engine, "paleo_elements")
    element_values = sorted({clean_text(value) for value in paleo_raw.name if clean_text(value)}, key=str.casefold)
    elements = pd.DataFrame([{"element_id": i, "symbol": symbol} for i, symbol in enumerate(element_values, 1)])
    insert_frame(engine, schema, "paleo_element", elements)
    element_lookup = {row.symbol: row.element_id for row in elements.itertuples(index=False)}
    counts["paleo_element"] = len(elements)

    protected_frames = []
    protected_defs = [
        ("ffh", "ffh.shp", "eu_cd_ph", "ms_cd_ph"),
        ("waterprotection", "waterprotection.shp", "eu_cd_pd", "ms_cd_pd"),
        ("recreation_areas", "recreation_areas.shp", "eu_cd_pr", "ms_cd_pr"),
    ]
    for table, source_file, id_col, state_col in protected_defs:
        raw = read_raw(engine, table, geo=True)
        protected_frames.append(gpd.GeoDataFrame({
            "protected_area_id": raw[id_col].map(key_text),
            "name": raw.name.map(clean_text),
            "area_type": raw.prot_type.map(clean_text),
            "legislation_code": raw.leg_cd.map(key_text),
            "member_state_code": raw[state_col].map(key_text),
            "status": raw.status.map(clean_text) if "status" in raw else None,
            "water_protection_zone": raw.wsg_zone.map(clean_text) if "wsg_zone" in raw else None,
            "area_status": raw.areastatus.map(clean_text) if "areastatus" in raw else None,
            "river_basin_code": raw.rbd_cd.map(key_text),
            "metadata_reference": raw.metadata.map(clean_text),
            "url": raw.url.map(clean_text),
            "source_file": source_file,
            "geometry": raw.geometry,
        }, geometry="geometry", crs=25833))
    protected = gpd.GeoDataFrame(pd.concat(protected_frames, ignore_index=True), geometry="geometry", crs=25833)
    insert_frame(engine, schema, "protected_area", protected, geo=True)
    protected_ids = set(protected.protected_area_id)
    counts["protected_area"] = len(protected)

    # Sampling sections preserve every source row.
    section_raw = observed_sources[2][1]
    section_records = []
    section_lookup: dict[tuple[str, str, int | None], int] = {}
    for row in section_raw.itertuples(index=False):
        water_id, code = key_text(row.eu_cd_lw), key_text(row.messstelle)
        section_id = int(row.source_row_number)
        year = integer(row.jahr)
        section_records.append({
            "sampling_section_id": section_id, "source_file": "einzeldaten_abschnitte.csv",
            "source_row_number": section_id, "water_body_id": water_id,
            "station_id": station_lookup.get((water_id, code)), "source_station_code": code,
            "section_code": key_text(row.abschnitt), "sampling_year": year,
            "length_from": number(row.laenge_von), "length_to": number(row.laenge_bis),
            "depth_from": number(row.tiefe_von), "depth_to": number(row.tiefe_bis),
        })
        section_lookup.setdefault((water_id, key_text(row.abschnitt), year), section_id)
    insert_frame(engine, schema, "sampling_section", pd.DataFrame(section_records))
    counts["sampling_section"] = len(section_records)

    measurement_records = []
    measurement_id = 0
    measurement_defs = [
        ("einzeldaten.csv", bio_raw, "parameter", "kommentar", True),
        ("einzeldaten_chemie.csv", chem_raw, "parameterbezeichnung", "kommentar", False),
    ]
    for source_file, raw, parameter_column, comment_column, biological in measurement_defs:
        for row in raw.itertuples(index=False):
            measurement_id += 1
            water_id, station_code = key_text(row.eu_cd_lw), key_text(row.messstelle)
            measured_at = date_value(row.datum)
            component = clean_text(row.qk)
            parameter_name = clean_text(getattr(row, parameter_column))
            unit_symbol = clean_text(row.einheit)
            section_code = key_text(row.id_abschnitt)
            parameter_id = parameter_lookup.get((parameter_name, component))
            if parameter_id is None:
                issue(issues, "measurement", source_file, row.source_row_number,
                      parameter_column, parameter_name, "unresolved_parameter", "Parameter konnte nicht aufgelöst werden")
                continue
            measurement_records.append({
                "measurement_id": measurement_id, "source_file": source_file,
                "source_row_number": int(row.source_row_number), "source_index": integer(row.unnamed_0),
                "water_body_id": water_id, "station_id": station_lookup.get((water_id, station_code)),
                "sampling_section_id": section_lookup.get((water_id, section_code, measured_at.year if measured_at else None)),
                "source_station_code": station_code, "source_section_code": section_code,
                "parameter_id": parameter_id, "unit_id": unit_lookup.get(unit_symbol),
                "measured_at": measured_at, "value": number(row.wert),
                "comment": clean_text(getattr(row, comment_column)),
                "method": clean_text(row.methode) if hasattr(row, "methode") else None,
                "monitoring_program": clean_text(row.messprogramm),
                "abundance_type": clean_text(row.abundanzart) if biological else None,
            })
    insert_frame(engine, schema, "measurement", pd.DataFrame(measurement_records))
    counts["measurement"] = len(measurement_records)

    def assessed_value(raw_value: Any) -> tuple[float | None, float | None, bool, str | None]:
        value = number(raw_value)
        if value is None or value == 999:
            return value, None, False, "NA/ungesichertes Ergebnis"
        if 1 <= value <= 5:
            return value, value, True, None
        return value, None, False, "außerhalb der angenommenen Skala 1–5"

    water_assessment_records = []
    for row in body_assessment_raw.itertuples(index=False):
        raw_value, normalized, valid, reason = assessed_value(row.wert)
        parameter_id = parameter_lookup[(clean_text(row.parameter), clean_text(row.qk))]
        water_assessment_records.append({
            "assessment_id": int(row.source_row_number), "source_file": "wasserkoerper_bewertung.csv",
            "source_row_number": int(row.source_row_number), "water_body_id": key_text(row.eu_cd_lw),
            "parameter_id": parameter_id, "assessment_year": integer(row.jahr),
            "value_raw": raw_value, "value_normalized": normalized, "is_valid": valid,
            "validation_reason": reason, "method": clean_text(row.methode),
        })
    insert_frame(engine, schema, "water_body_assessment", pd.DataFrame(water_assessment_records))
    counts["water_body_assessment"] = len(water_assessment_records)

    station_assessment_records = []
    for row in assessment_raw.itertuples(index=False):
        water_id, station_code = key_text(row.eu_cd_lw), key_text(row.messstelle)
        raw_value, normalized, valid, reason = assessed_value(row.wert)
        parameter_id = parameter_lookup[(clean_text(row.parameter), clean_text(row.komponente))]
        station_assessment_records.append({
            "assessment_id": int(row.source_row_number), "source_file": "messstellen_bewertung.csv",
            "source_row_number": int(row.source_row_number), "water_body_id": water_id,
            "station_id": station_lookup.get((water_id, station_code)), "source_station_code": station_code,
            "parameter_id": parameter_id, "unit_id": unit_lookup.get(clean_text(row.einheit)),
            "assessment_year": integer(row.jahr), "value_raw": raw_value,
            "value_normalized": normalized, "is_valid": valid, "validation_reason": reason,
            "description": clean_text(row.beschreibung), "method": clean_text(row.methode),
            "expert_judgement": clean_text(row.expertenurteil), "comment": clean_text(row.kommentar),
            "reported_x": number(row.x_wert), "reported_y": number(row.y_wert),
        })
    insert_frame(engine, schema, "station_assessment", pd.DataFrame(station_assessment_records))
    counts["station_assessment"] = len(station_assessment_records)

    chemical_records = []
    chemical_id = 0
    for row in gw_status_raw.itertuples(index=False):
        chemical_id += 1
        chemical_records.append({
            "chemical_status_id": chemical_id, "source_file": "GW_measures.csv",
            "source_row_number": int(row.source_row_number), "surface_water_body_id": None,
            "groundwater_body_id": key_text(row.eu_cd_gb),
            "pollutant_id": pollutant_lookup[key_text(row.pollcode)], "failed_standard": bool_code(row.poll_fail),
            "risk_code": key_text(row.poll_risk), "upward_trend_code": key_text(row.pollupward),
            "trend_code": key_text(row.polltrendr), "trend_reversal_code": key_text(row.polltrer_p),
            "exemption_type": key_text(row.ex_che_typ), "exemption_reason": key_text(row.ex_che_pr),
            "exception_code": key_text(row.pollexntc), "level_set": clean_text(row.levelset),
            "level_value": number(row.levelvalue) if number(row.levelvalue) is not None else math.nan,
            "level_unit": clean_text(row.levelunit),
            "fail_reason_code": None, "fail_reason_other": None,
            "inserted_at": date_value(row.ins_when), "inserted_by": clean_text(row.ins_by),
            "river_basin_code": key_text(row.rbd_cd), "metadata_reference": clean_text(row.metadata),
        })
    identifier_lookup = {item["identifier"]: item["water_body_id"] for item in identifiers}
    for row in ow_status_raw.itertuples(index=False):
        chemical_id += 1
        source_code = key_text(row.eu_cd_ls)
        water_id = identifier_lookup.get(source_code)
        if water_id is None:
            issue(issues, "chemical_status", "OW_measures.csv", row.source_row_number,
                  "EU_CD_LS", source_code, "unresolved_water_body", "EU_CD_LW/EU_CD_LS konnte nicht aufgelöst werden")
            continue
        chemical_records.append({
            "chemical_status_id": chemical_id, "source_file": "OW_measures.csv",
            "source_row_number": int(row.source_row_number), "surface_water_body_id": water_id,
            "groundwater_body_id": None, "pollutant_id": pollutant_lookup[key_text(row.pscode)],
            "failed_standard": bool_code(row.ps_fail), "risk_code": None,
            "upward_trend_code": None, "trend_code": None, "trend_reversal_code": None,
            "exemption_type": key_text(row.chemextype), "exemption_reason": key_text(row.chemexpre),
            "exception_code": key_text(row.ps_exctype), "level_set": None, "level_value": math.nan,
            "level_unit": None, "fail_reason_code": key_text(row.failrbsp),
            "fail_reason_other": clean_text(row.failrbs_ot), "inserted_at": date_value(row.ins_when),
            "inserted_by": clean_text(row.ins_by), "river_basin_code": key_text(row.rbd_cd),
            "metadata_reference": clean_text(row.metadata),
        })
    insert_frame(engine, schema, "chemical_status", pd.DataFrame(chemical_records))
    counts["chemical_status"] = len(chemical_records)

    paleo_records = [{
        "paleo_measurement_id": int(row.source_row_number), "source_file": "paleo_elements.csv",
        "source_row_number": int(row.source_row_number), "core_code": key_text(row.code),
        "element_id": element_lookup[clean_text(row.name)], "age_years_bp": integer(row.ages),
        "value_dimensionless": number(row.value),
    } for row in paleo_raw.itertuples(index=False)]
    insert_frame(engine, schema, "paleo_measurement", pd.DataFrame(paleo_records))
    counts["paleo_measurement"] = len(paleo_records)

    protection_raw = read_raw(engine, "protection_area_bewertung")
    protection_records = []
    for row in protection_raw.itertuples(index=False):
        area_code, water_code = key_text(row.eu_cd_p), key_text(row.eu_cd_wb)
        protection_records.append({
            "assessment_id": int(row.source_row_number), "source_file": "protection_area_bewertung.csv",
            "source_row_number": int(row.source_row_number), "source_protected_area_code": area_code,
            "source_water_body_code": water_code,
            "protected_area_id": area_code if area_code in protected_ids else None,
            "surface_water_body_id": identifier_lookup.get(water_code),
            "groundwater_body_id": water_code if water_code in groundwater_ids else None,
            "template": clean_text(row.template), "name": clean_text(row.nametext),
            "legislation_code": key_text(row.leg_cd), "legislation_link": clean_text(row.legislink),
            "area_type": key_text(row.parea_t), "designation_begin": date_value(row.desigbegin),
            "evolution_type": clean_text(row.evolutiont), "comment": clean_text(row.pa_comment),
            "exemption_code": key_text(row.parea_ex), "habitat_status": key_text(row.parea_hb_o),
            "metadata_reference": clean_text(row.metadata),
        })
    insert_frame(engine, schema, "protected_area_assessment", pd.DataFrame(protection_records))
    counts["protected_area_assessment"] = len(protection_records)

    # Spatial relations are facts about geometry only; no chemical condition is
    # inferred from these overlaps.
    spatial_sql = [
        f'''INSERT INTO "{schema}".water_body_groundwater_body
            SELECT w.water_body_id,g.groundwater_body_id,
              ST_Area(ST_Intersection(w.geometry,g.geometry))/1000000.0,
              ST_Area(ST_Intersection(w.geometry,g.geometry))/NULLIF(ST_Area(w.geometry),0),
              'spatial_intersection'
            FROM "{schema}".water_body w JOIN "{schema}".groundwater_body g
              ON ST_Intersects(w.geometry,g.geometry)''',
        f'''INSERT INTO "{schema}".catchment_groundwater_body
            SELECT c.catchment_id,g.groundwater_body_id,
              ST_Area(ST_Intersection(c.geometry,g.geometry))/1000000.0,
              ST_Area(ST_Intersection(c.geometry,g.geometry))/NULLIF(ST_Area(c.geometry),0),
              'spatial_intersection'
            FROM "{schema}".catchment c JOIN "{schema}".groundwater_body g
              ON ST_Intersects(c.geometry,g.geometry)''',
        f'''INSERT INTO "{schema}".catchment_water_body
            SELECT c.catchment_id,w.water_body_id,
              ST_Area(ST_Intersection(c.geometry,w.geometry))/1000000.0,
              ST_Area(ST_Intersection(c.geometry,w.geometry))/NULLIF(ST_Area(c.geometry),0),
              ST_Area(ST_Intersection(c.geometry,w.geometry))/NULLIF(ST_Area(w.geometry),0),
              'spatial_intersection'
            FROM "{schema}".catchment c JOIN "{schema}".water_body w
              ON ST_Intersects(c.geometry,w.geometry)''',
        f'''INSERT INTO "{schema}".station_groundwater_body
            SELECT s.station_id,g.groundwater_body_id,'spatial_intersection'
            FROM "{schema}".monitoring_station s JOIN "{schema}".groundwater_body g
              ON s.location IS NOT NULL AND ST_Intersects(s.location,g.geometry)''',
        f'''INSERT INTO "{schema}".station_catchment
            SELECT s.station_id,c.catchment_id,'spatial_intersection'
            FROM "{schema}".monitoring_station s JOIN "{schema}".catchment c
              ON s.location IS NOT NULL AND ST_Intersects(s.location,c.geometry)''',
        f'''INSERT INTO "{schema}".water_body_protected_area
            SELECT w.water_body_id,p.protected_area_id,'spatial_intersection',
              ST_Area(ST_Intersection(w.geometry,p.geometry))/1000000.0,
              ST_Area(ST_Intersection(w.geometry,p.geometry))/NULLIF(ST_Area(w.geometry),0)
            FROM "{schema}".water_body w JOIN "{schema}".protected_area p
              ON ST_Intersects(w.geometry,p.geometry)''',
        f'''INSERT INTO "{schema}".water_body_protected_area
            SELECT DISTINCT a.surface_water_body_id,a.protected_area_id,'source_reference',
              NULL::double precision,NULL::double precision
            FROM "{schema}".protected_area_assessment a
            WHERE a.surface_water_body_id IS NOT NULL AND a.protected_area_id IS NOT NULL
            ON CONFLICT DO NOTHING''',
    ]
    with engine.begin() as connection:
        for statement in spatial_sql:
            connection.exec_driver_sql(statement)
        for table in (
            "water_body_groundwater_body", "catchment_groundwater_body", "catchment_water_body",
            "station_groundwater_body", "station_catchment", "water_body_protected_area",
        ):
            counts[table] = int(connection.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"')).scalar_one())

    insert_frame(engine, schema, "import_issue", pd.DataFrame(issues))
    counts["import_issue"] = len(issues)
    return counts


def validate_schema(engine, schema: str, counts: dict[str, int]) -> list[str]:
    expected = {
        "water_body": 6, "groundwater_body": 4, "planning_unit": 4,
        "catchment": 32, "core_location": 6, "sampling_section": 8194,
        "measurement": 118399, "water_body_assessment": 26,
        "station_assessment": 2906, "chemical_status": 25,
        "protected_area": 71, "protected_area_assessment": 74,
        "paleo_measurement": 2400,
    }
    errors = [f"{table}: {counts.get(table)} rows, expected {expected_count}"
              for table, expected_count in expected.items() if counts.get(table) != expected_count]
    with engine.connect() as connection:
        fk_count = int(connection.execute(text("""
            SELECT count(*) FROM information_schema.table_constraints
            WHERE table_schema=:schema AND constraint_type='FOREIGN KEY'
        """), {"schema": schema}).scalar_one())
        if fk_count < 35:
            errors.append(f"only {fk_count} foreign-key constraints found")
        for table in expected:
            actual = int(connection.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"')).scalar_one())
            if actual != expected[table]:
                errors.append(f"database {table}: {actual} rows, expected {expected[table]}")
    return errors


def publish_schema(engine, rebuild: bool = False) -> dict[str, int]:
    inspector = inspect(engine)
    if TARGET_SCHEMA in inspector.get_schema_names() and not rebuild:
        raise RuntimeError(
            f"Schema {TARGET_SCHEMA} already exists. Use --rebuild to replace it after a successful new build."
        )
    create_schema(engine, BUILD_SCHEMA)
    counts = build_schema(engine, BUILD_SCHEMA)
    errors = validate_schema(engine, BUILD_SCHEMA, counts)
    if errors:
        raise RuntimeError("water_kg_2 validation failed:\n- " + "\n- ".join(errors))
    with engine.begin() as connection:
        if TARGET_SCHEMA in inspect(connection).get_schema_names():
            connection.exec_driver_sql(f'DROP SCHEMA "{TARGET_SCHEMA}" CASCADE')
        connection.exec_driver_sql(f'ALTER SCHEMA "{BUILD_SCHEMA}" RENAME TO "{TARGET_SCHEMA}"')
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Build water_kg_2 from water_raw")
    parser.add_argument("--rebuild", action="store_true", help="Replace an existing water_kg_2 after validation")
    args = parser.parse_args()
    engine = database_engine()
    try:
        counts = publish_schema(engine, rebuild=args.rebuild)
        for table, count in sorted(counts.items()):
            print(f"{TARGET_SCHEMA}.{table}: {count} rows")
        print("water_kg_2 validation: OK")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()

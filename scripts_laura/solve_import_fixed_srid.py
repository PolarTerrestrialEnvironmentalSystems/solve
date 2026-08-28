import os
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiPolygon
from sqlalchemy import create_engine, inspect, text

from raw_data_import import load_raw_sources


PATH = Path(r"C:\Users\lschild\Documents\SOLVE")
data = PATH / "dummy_data"
SCHEMA = "water_kg"


# -----------------------------------------------------------------------------
# Database connection / initialization
# -----------------------------------------------------------------------------

def conn_db():
    host = "localhost"
    port = 5432
    database = "SOLVE"
    user = "postgres"
    password = os.getenv("SOLVE_DB_PASSWORD", "postgres")

    engine = create_engine(
        f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"
    )
    with engine.connect():
        print("Verbindung erfolgreich")
    return engine


DDL_STATEMENTS = [
    f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"',
    'CREATE EXTENSION IF NOT EXISTS postgis',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.water_body (
        eu_cd_lw TEXT PRIMARY KEY,
        eu_cd_ls TEXT,
        water_body_type TEXT,
        name TEXT,
        lawa_id BIGINT,
        area_km2 DOUBLE PRECISION,
        volume_m3 DOUBLE PRECISION,
        max_depth_m DOUBLE PRECISION,
        artificial TEXT,
        modified TEXT,
        river_basin_code TEXT,
        planning_unit_code TEXT,
        water_type BIGINT,
        geometry geometry(Geometry, 25833)
    )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.groundwater_body (
        eu_cd_gb TEXT PRIMARY KEY,
        name TEXT,
        horizon INTEGER,
        aquifer_type TEXT,
        geological_formation TEXT,
        layered TEXT,
        river_basin_code TEXT,
        planning_unit_code TEXT,
        quantitative_status TEXT,
        chemical_status TEXT,
        metadata_reference TEXT,
        geometry geometry(Geometry, 25833)
    )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.catchment (
        catchment_id TEXT PRIMARY KEY,
        name TEXT,
        area_km2 DOUBLE PRECISION,
        area_number TEXT,
        water_body_id TEXT,
        planning_unit_code TEXT,
        river_basin_code TEXT,
        water_authority TEXT,
        description_from TEXT,
        description_to TEXT,
        comment TEXT,
        metadata_reference TEXT,
        geometry geometry(Geometry, 25833)
    )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.monitoring_station (
        station_id BIGSERIAL PRIMARY KEY,
        station_code TEXT NOT NULL,
        name TEXT,
        water_body_id TEXT,
        location geometry(Geometry, 25833),
        CONSTRAINT monitoring_station_natural_key UNIQUE (station_code, water_body_id)
    )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.parameter (
        parameter_id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        quality_component TEXT,
        unit TEXT
    )''',
    # NULL values make a normal UNIQUE constraint unsuitable for the parameter
    # natural key, so create a NULL-safe unique expression index.
    f'''CREATE UNIQUE INDEX IF NOT EXISTS uq_parameter_natural_key
        ON {SCHEMA}.parameter (
            name,
            COALESCE(quality_component, ''),
            COALESCE(unit, '')
        )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.sampling_section (
        sampling_section_id BIGSERIAL PRIMARY KEY,
        water_body_id TEXT,
        source_station_code TEXT,
        section_code TEXT,
        sampling_year INTEGER,
        length_from DOUBLE PRECISION,
        length_to DOUBLE PRECISION,
        depth_from DOUBLE PRECISION,
        depth_to DOUBLE PRECISION,
        station_id BIGINT
    )''',
    f'''CREATE UNIQUE INDEX IF NOT EXISTS uq_sampling_section_natural_key
        ON {SCHEMA}.sampling_section (
            COALESCE(water_body_id, ''),
            COALESCE(source_station_code, ''),
            COALESCE(section_code, ''),
            COALESCE(sampling_year, -1)
        )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.measurement (
        measurement_id BIGSERIAL PRIMARY KEY,
        water_body_id TEXT,
        source_station_code TEXT,
        sampling_section TEXT,
        measured_at DATE,
        value DOUBLE PRECISION,
        comment TEXT,
        method TEXT,
        monitoring_program TEXT,
        abundance_type TEXT,
        station_id BIGINT,
        parameter_id BIGINT
    )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.water_body_assessment (
        assessment_id BIGSERIAL PRIMARY KEY,
        water_body_id TEXT,
        parameter TEXT,
        assessment_year INTEGER,
        value DOUBLE PRECISION,
        method TEXT,
        quality_component TEXT
    )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.station_assessment (
        assessment_id BIGSERIAL PRIMARY KEY,
        water_body_id TEXT,
        source_station_code TEXT,
        parameter TEXT,
        assessment_year INTEGER,
        value DOUBLE PRECISION,
        method TEXT,
        quality_component TEXT,
        unit TEXT,
        description TEXT,
        expert_judgement TEXT,
        comment TEXT,
        reported_x DOUBLE PRECISION,
        reported_y DOUBLE PRECISION,
        station_id BIGINT
    )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.pollutant (
        pollutant_id BIGSERIAL PRIMARY KEY,
        pollutant_code TEXT NOT NULL UNIQUE
    )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.water_body_chemical_status (
        chemical_status_id BIGSERIAL PRIMARY KEY,
        surface_water_body_id TEXT,
        groundwater_body_id TEXT,
        pollutant_id BIGINT,
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
        metadata_reference TEXT
    )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.protected_area (
        protected_area_id TEXT PRIMARY KEY,
        name TEXT,
        area_type TEXT,
        legislation_code TEXT,
        legislation_name TEXT,
        legislation_url TEXT,
        geometry geometry(Geometry, 25833)
    )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.water_body_protected_area (
        water_body_id TEXT NOT NULL,
        protected_area_id TEXT NOT NULL,
        relation_method TEXT NOT NULL,
        evolution_type TEXT,
        exemption_code TEXT,
        comment TEXT,
        PRIMARY KEY (water_body_id, protected_area_id, relation_method)
    )''',

    # Spatial relationship tables used by load_spatial_relations().
    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.station_groundwater_body (
        station_id BIGINT NOT NULL,
        groundwater_body_id TEXT NOT NULL,
        relation_type TEXT,
        determination_method TEXT,
        PRIMARY KEY (station_id, groundwater_body_id)
    )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.water_body_groundwater_body (
        water_body_id TEXT NOT NULL,
        groundwater_body_id TEXT NOT NULL,
        relation_type TEXT,
        overlap_area_km2 DOUBLE PRECISION,
        water_body_overlap_ratio DOUBLE PRECISION,
        determination_method TEXT,
        PRIMARY KEY (water_body_id, groundwater_body_id)
    )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.catchment_groundwater_body (
        catchment_id TEXT NOT NULL,
        groundwater_body_id TEXT NOT NULL,
        relation_type TEXT,
        overlap_area_km2 DOUBLE PRECISION,
        catchment_overlap_ratio DOUBLE PRECISION,
        determination_method TEXT,
        PRIMARY KEY (catchment_id, groundwater_body_id)
    )''',

    f'''CREATE TABLE IF NOT EXISTS {SCHEMA}.station_catchment (
        station_id BIGINT NOT NULL,
        catchment_id TEXT NOT NULL,
        relation_type TEXT,
        determination_method TEXT,
        PRIMARY KEY (station_id, catchment_id)
    )''',
]


SURROGATE_ID_COLUMNS = {
    "monitoring_station": "station_id",
    "parameter": "parameter_id",
    "sampling_section": "sampling_section_id",
    "measurement": "measurement_id",
    "water_body_assessment": "assessment_id",
    "station_assessment": "assessment_id",
    "pollutant": "pollutant_id",
    "water_body_chemical_status": "chemical_status_id",
}


def initialize_database(engine=None):
    """Create the PostGIS schema and migrate missing structural columns."""
    engine = engine or conn_db()
    with engine.begin() as connection:
        for statement in DDL_STATEMENTS:
            connection.exec_driver_sql(statement)

        # CREATE TABLE IF NOT EXISTS does not alter tables that already existed.
        # These ALTER statements migrate the surrogate-ID columns that the
        # loader code relies on. BIGSERIAL also back-fills existing rows.
        id_migrations = {
            "monitoring_station": "station_id",
            "parameter": "parameter_id",
            "sampling_section": "sampling_section_id",
            "measurement": "measurement_id",
            "water_body_assessment": "assessment_id",
            "station_assessment": "assessment_id",
            "pollutant": "pollutant_id",
            "water_body_chemical_status": "chemical_status_id",
        }
        for table, column in id_migrations.items():
            connection.exec_driver_sql(
                f'ALTER TABLE "{SCHEMA}"."{table}" '
                f'ADD COLUMN IF NOT EXISTS "{column}" BIGSERIAL'
            )

        # Older versions created PostGIS columns as bare ``geometry``, which
        # gives them SRID 0.  All source layers used by this project are stored
        # in / converted to EPSG:25833, so migrate those columns to that SRID.
        spatial_columns = {
            "water_body": "geometry",
            "groundwater_body": "geometry",
            "catchment": "geometry",
            "monitoring_station": "location",
            "protected_area": "geometry",
        }
        for table, column in spatial_columns.items():
            connection.exec_driver_sql(
                f'ALTER TABLE "{SCHEMA}"."{table}" '
                f'ALTER COLUMN "{column}" TYPE geometry(Geometry, 25833) '
                f'USING CASE WHEN "{column}" IS NULL THEN NULL '
                f'WHEN ST_SRID("{column}") = 0 THEN ST_SetSRID("{column}", 25833) '
                f'WHEN ST_SRID("{column}") = 25833 THEN "{column}" '
                f'ELSE ST_Transform("{column}", 25833) END'
            )

        # Indexes needed by ON CONFLICT and by natural-key lookups.
        connection.exec_driver_sql(
            f'CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_station_natural_key_idx '
            f'ON {SCHEMA}.monitoring_station (station_code, water_body_id)'
        )
        connection.exec_driver_sql(
            f'CREATE UNIQUE INDEX IF NOT EXISTS uq_pollutant_code_idx '
            f'ON {SCHEMA}.pollutant (pollutant_code)'
        )

    print("Datenbankschema ist initialisiert bzw. aktualisiert.")
    return engine


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------

def as_multipolygon(geometry):
    if geometry is None:
        return None
    if geometry.geom_type == "MultiPolygon":
        return geometry
    if geometry.geom_type == "Polygon":
        return MultiPolygon([geometry])
    return geometry


def text_column(series):
    return series.astype("string").str.strip()


def _sql_type_from_series(series):
    """Best-effort PostgreSQL type for a newly encountered non-geometry column."""
    if pd.api.types.is_bool_dtype(series.dtype):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(series.dtype):
        return "BIGINT"
    if pd.api.types.is_float_dtype(series.dtype):
        return "DOUBLE PRECISION"
    if pd.api.types.is_datetime64_any_dtype(series.dtype):
        return "TIMESTAMP"
    return "TEXT"


def ensure_missing_columns(element, connection, name, schema=SCHEMA, geo=False):
    """Add DataFrame columns to an existing table if they are missing."""
    inspector = inspect(connection)
    existing_columns = {
        c["name"] for c in inspector.get_columns(name, schema=schema)
    }

    geometry_name = None
    if geo and isinstance(element, gpd.GeoDataFrame):
        geometry_name = element.geometry.name

    for column in element.columns:
        if column in existing_columns:
            continue

        if column == geometry_name:
            sql_type = "geometry(Geometry, 25833)"
        else:
            sql_type = _sql_type_from_series(element[column])

        connection.exec_driver_sql(
            f'ALTER TABLE "{schema}"."{name}" '
            f'ADD COLUMN IF NOT EXISTS "{column}" {sql_type}'
        )


def update_db(element, engine, name, primary_key, geo=False, schema=SCHEMA):
    """
    Insert only rows whose natural key is not already present.

    The database initializer creates the expected tables. This function also
    creates an unknown table as a fallback and adds newly encountered columns
    to an existing table.
    """
    if isinstance(primary_key, str):
        primary_key = [primary_key]

    missing_keys = [c for c in primary_key if c not in element.columns]
    if missing_keys:
        raise ValueError(
            f"{name}: primary-key columns missing from input: {missing_keys}. "
            f"Available columns: {element.columns.tolist()}"
        )

    element = element.drop_duplicates(subset=primary_key).copy()

    # Preserve GeoDataFrame metadata after operations such as merge/filtering.
    if geo and not isinstance(element, gpd.GeoDataFrame):
        if "geometry" in element.columns:
            element = gpd.GeoDataFrame(element, geometry="geometry")
        elif "location" in element.columns:
            element = gpd.GeoDataFrame(element, geometry="location")
        else:
            raise ValueError(f"{name}: geo=True but no geometry column is present")

    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        inspector = inspect(connection)
        table_exists = inspector.has_table(name, schema=schema)

        if not table_exists:
            if geo:
                element.to_postgis(
                    name=name,
                    con=connection,
                    schema=schema,
                    if_exists="fail",
                    index=False,
                )
            else:
                element.to_sql(
                    name=name,
                    con=connection,
                    schema=schema,
                    if_exists="fail",
                    index=False,
                )
            print(f"Created {schema}.{name} and inserted {len(element)} rows")
            return element

        ensure_missing_columns(element, connection, name, schema=schema, geo=geo)

        existing = pd.read_sql_table(name, con=connection, schema=schema)
        existing_keys = existing[primary_key].drop_duplicates()

        new_rows = element.merge(
            existing_keys,
            on=primary_key,
            how="left",
            indicator=True,
        )
        new_rows = new_rows.loc[new_rows["_merge"] == "left_only"].drop(columns="_merge")

        if new_rows.empty:
            print(f"No new rows for {schema}.{name}")
            return new_rows

        if geo:
            geometry_name = element.geometry.name
            new_rows = gpd.GeoDataFrame(
                new_rows,
                geometry=geometry_name,
                crs=element.crs,
            )
            new_rows.to_postgis(
                name=name,
                con=connection,
                schema=schema,
                if_exists="append",
                index=False,
            )
        else:
            new_rows.to_sql(
                name=name,
                con=connection,
                schema=schema,
                if_exists="append",
                index=False,
            )

        print(f"{len(new_rows)} new rows inserted into {schema}.{name}")
        return new_rows


# -----------------------------------------------------------------------------
# Loaders
# -----------------------------------------------------------------------------

def load_waterbody(gdf, stammdaten):
    engine = conn_db()
    gdf = gdf.to_crs(25833) if gdf.crs and gdf.crs.to_epsg() != 25833 else gdf
    waterbody = gpd.GeoDataFrame(
        {
            "eu_cd_lw": gdf["EU_CD_LW"],
            "eu_cd_ls": gdf["EU_CD_LS"],
            "water_body_type": "lake",
            "name": gdf["S_NAME"],
            "lawa_id": stammdaten["LAWA_ID"],
            # FLAECHE_ATKIS is supplied in hectares; the target column is km².
            "area_km2": pd.to_numeric(
                stammdaten["FLAECHE_ATKIS"], errors="coerce"
            ) / 100.0,
            "volume_m3": stammdaten["VOLUMEN"],
            "max_depth_m": stammdaten["Z_MAX"],
            "artificial": stammdaten["ARTIFICIAL_2021"],
            "modified": stammdaten["MODIFIED_2021"],
            "river_basin_code": gdf["RBD_CD"],
            "planning_unit_code": gdf["PLANU_CD"],
            "water_type": stammdaten["TYP_2021"],
        },
        geometry=gdf.geometry,
        crs=gdf.crs,
    )
    update_db(waterbody, engine, "water_body", "eu_cd_lw", geo=True)
    return waterbody


def load_groundwaterbody(gwb):
    engine = conn_db()
    gwb = gwb.to_crs(25833) if gwb.crs and gwb.crs.to_epsg() != 25833 else gwb
    groundwaterbody = gpd.GeoDataFrame(
        {
            "eu_cd_gb": gwb["EU_CD_GB"],
            "name": gwb["NAMETEXT"],
            "horizon": pd.to_numeric(gwb["HORIZON"], errors="coerce").astype("Int64"),
            "aquifer_type": gwb["AQUI_TYPE"],
            "geological_formation": gwb["GEOL_FORM"],
            "layered": gwb["LAYERED"],
            "river_basin_code": gwb["RBD_CD"],
            "planning_unit_code": gwb["PLANU_CD"],
            "quantitative_status": gwb["QUANT_STAT"],
            "chemical_status": gwb["CHEM_STAT"],
            "metadata_reference": gwb["METADATA"],
        },
        geometry=gwb.geometry,
        crs=gwb.crs,
    )
    update_db(groundwaterbody, engine, "groundwater_body", "eu_cd_gb", geo=True)
    return groundwaterbody


def load_catchments(source):
    engine = conn_db()
    catchments = gpd.GeoDataFrame(
        {
            "catchment_id": source["DRAIN_C"],
            "name": source["DRAIN_N"],
            "area_km2": pd.to_numeric(source["AREA_CA"], errors="coerce"),
            "area_number": source["AREA_NO"],
            "water_body_id": source["EU_CD_W"],
            "planning_unit_code": source["PLANU_C"],
            "river_basin_code": source["RBD_CD"],
            "water_authority": source["WA_CD"],
            "description_from": source["DESCR_F"],
            "description_to": source["DESCR_T"],
            "comment": source["COMMENT"],
            "metadata_reference": source["METADATA"],
        },
        geometry=source.geometry,
        crs=source.crs,
    )
    update_db(catchments, engine, "catchment", "catchment_id", geo=True)
    return catchments


def load_monitoring_stations(source):
    engine = conn_db()
    source = source.to_crs(25833) if source.crs and source.crs.to_epsg() != 25833 else source
    stations = gpd.GeoDataFrame(
        {
            "station_code": source["EU_CD_SM"],
            "name": source["NAME_STN"],
            "water_body_id": source["EU_CD_WB"],
        },
        geometry=source.geometry,
        crs=source.crs,
    ).rename_geometry("location")

    # station_code alone may occur in more than one water body, so use both.
    update_db(
        stations,
        engine,
        "monitoring_station",
        ["station_code", "water_body_id"],
        geo=True,
    )
    return stations


def load_parameters(bio, chem):
    engine = conn_db()

    bio_param = bio.rename(
        columns={"PARAMETER": "name", "QK": "quality_component", "EINHEIT": "unit"}
    )[["name", "quality_component", "unit"]].copy()

    chem_param = chem.rename(
        columns={
            "Parameterbezeichnung": "name",
            "QK": "quality_component",
            "EINHEIT": "unit",
        }
    )[["name", "quality_component", "unit"]].copy()

    parameter = pd.concat([bio_param, chem_param], ignore_index=True)
    parameter = parameter.drop_duplicates(subset=["name", "quality_component", "unit"])

    update_db(
        parameter,
        engine,
        "parameter",
        ["name", "quality_component", "unit"],
        geo=False,
    )
    return parameter


def station_ids():
    engine = conn_db()
    stations = pd.read_sql_table("monitoring_station", engine, schema=SCHEMA)

    required = {"station_id", "station_code", "water_body_id", "name"}
    missing = required - set(stations.columns)
    if missing:
        raise RuntimeError(
            f"monitoring_station is missing columns {sorted(missing)}. "
            "Run initialize_database() before loading data."
        )

    result = {}
    for row in stations.itertuples(index=False):
        result[(str(row.station_code), row.water_body_id)] = row.station_id
        if pd.notna(row.name):
            result[(str(row.name), row.water_body_id)] = row.station_id
    return result


def load_sampling_sections(source):
    engine = conn_db()
    lookup = station_ids()
    sections = pd.DataFrame(
        {
            "water_body_id": source["EU_CD_LW"],
            "source_station_code": source["MESSSTELLE"],
            "section_code": source["ABSCHNITT"],
            "sampling_year": pd.to_numeric(source["JAHR"], errors="coerce").astype("Int64"),
            "length_from": pd.to_numeric(source["LAENGE_VON"], errors="coerce"),
            "length_to": pd.to_numeric(source["LAENGE_BIS"], errors="coerce"),
            "depth_from": pd.to_numeric(source["TIEFE_VON"], errors="coerce"),
            "depth_to": pd.to_numeric(source["TIEFE_BIS"], errors="coerce"),
        }
    )
    sections["station_id"] = sections.apply(
        lambda row: lookup.get((str(row["source_station_code"]), row["water_body_id"])),
        axis=1,
    )
    return update_db(
        sections,
        engine,
        "sampling_section",
        ["water_body_id", "source_station_code", "section_code", "sampling_year"],
        geo=False,
    )


def load_measurements(bio, chem):
    engine = conn_db()
    parameters = pd.read_sql_table("parameter", engine, schema=SCHEMA)

    parameter_lookup = {
        (
            str(row.name),
            None if pd.isna(row.quality_component) else str(row.quality_component),
            None if pd.isna(row.unit) else str(row.unit),
        ): row.parameter_id
        for row in parameters.itertuples(index=False)
    }
    stations = station_ids()

    def convert(source, parameter_column, comment_column, biological=False):
        result = pd.DataFrame(
            {
                "water_body_id": source["EU_CD_LW"],
                "source_station_code": source["MESSSTELLE"],
                "sampling_section": source["ID_ABSCHNITT"],
                "measured_at": pd.to_datetime(source["DATUM"], errors="coerce").dt.date,
                "value": pd.to_numeric(source["WERT"], errors="coerce"),
                "comment": source[comment_column],
                "method": source["METHODE"] if "METHODE" in source.columns else None,
                "monitoring_program": source["MESSPROGRAMM"],
                "abundance_type": source["ABUNDANZART"] if biological else None,
            }
        )
        result["station_id"] = result.apply(
            lambda row: stations.get((str(row["source_station_code"]), row["water_body_id"])),
            axis=1,
        )
        result["parameter_id"] = source.apply(
            lambda row: parameter_lookup.get(
                (
                    str(row[parameter_column]),
                    None if pd.isna(row["QK"]) else str(row["QK"]),
                    None if pd.isna(row["EINHEIT"]) else str(row["EINHEIT"]),
                )
            ),
            axis=1,
        )
        return result.dropna(subset=["water_body_id", "parameter_id"])

    measurements = pd.concat(
        [
            convert(bio, "PARAMETER", "KOMMENTAR", biological=True),
            convert(chem, "Parameterbezeichnung", "Kommentar"),
        ],
        ignore_index=True,
    )

    return update_db(
        measurements,
        engine,
        "measurement",
        [
            "water_body_id",
            "source_station_code",
            "parameter_id",
            "measured_at",
            "sampling_section",
            "value",
        ],
    )


def load_assessments(source):
    engine = conn_db()
    body_source = pd.read_csv(data / "wasserkoerper_bewertung.csv", low_memory=False)

    body = pd.DataFrame(
        {
            "water_body_id": body_source["EU_CD_LW"],
            "parameter": body_source["PARAMETER"],
            "assessment_year": pd.to_numeric(body_source["JAHR"], errors="coerce").astype("Int64"),
            "value": pd.to_numeric(body_source["WERT"], errors="coerce"),
            "method": body_source["METHODE"],
            "quality_component": body_source["QK"],
        }
    ).dropna(subset=["water_body_id", "parameter"])

    update_db(
        body,
        engine,
        "water_body_assessment",
        ["water_body_id", "parameter", "assessment_year", "method"],
    )

    lookup = station_ids()
    station = pd.DataFrame(
        {
            "water_body_id": source["EU_CD_LW"],
            "source_station_code": source["MESSSTELLE"],
            "parameter": source["PARAMETER"],
            "assessment_year": pd.to_numeric(source["JAHR"], errors="coerce").astype("Int64"),
            "value": pd.to_numeric(source["WERT"], errors="coerce"),
            "method": source["METHODE"],
            "quality_component": source["KOMPONENTE"],
            "unit": source["EINHEIT"],
            "description": source["BESCHREIBUNG"],
            "expert_judgement": source["EXPERTENURTEIL"],
            "comment": source["KOMMENTAR"],
            "reported_x": pd.to_numeric(source["X_WERT"], errors="coerce"),
            "reported_y": pd.to_numeric(source["Y_WERT"], errors="coerce"),
        }
    )
    station["station_id"] = station.apply(
        lambda row: lookup.get((str(row["source_station_code"]), row["water_body_id"])),
        axis=1,
    )
    station = station.dropna(subset=["water_body_id", "parameter"])

    update_db(
        station,
        engine,
        "station_assessment",
        ["water_body_id", "source_station_code", "parameter", "assessment_year", "method"],
    )
    return body, station


def load_chemical_status(gw, ow):
    engine = conn_db()

    codes = pd.concat([gw["POLLCODE"], ow["PSCODE"]])
    pollutants = pd.DataFrame({"pollutant_code": codes.dropna().astype("string").str.strip().unique()})
    update_db(pollutants, engine, "pollutant", ["pollutant_code"])

    pollutant_table = pd.read_sql_table("pollutant", engine, schema=SCHEMA)
    pollutant_ids = dict(zip(pollutant_table.pollutant_code.astype(str), pollutant_table.pollutant_id))

    water_table = pd.read_sql_table("water_body", engine, schema=SCHEMA)
    # OW_measures calls its column EU_CD_LS, but the supplied values are a
    # mixture of lake-water-body codes (EU_CD_LW) and segment codes (EU_CD_LS).
    lake_ids = dict(zip(water_table.eu_cd_lw.astype(str), water_table.eu_cd_lw))
    lake_ids.update(zip(water_table.eu_cd_ls.astype(str), water_table.eu_cd_lw))

    ground = pd.DataFrame(
        {
            "surface_water_body_id": None,
            "groundwater_body_id": text_column(gw["EU_CD_GB"]),
            "pollutant_id": text_column(gw["POLLCODE"]).map(pollutant_ids),
            "failed_standard": gw["POLL_FAIL"].map({"Y": True, "N": False}),
            "risk_code": text_column(gw["POLL_RISK"]),
            "upward_trend_code": text_column(gw["POLLUPWARD"]),
            "trend_code": text_column(gw["POLLTRENDR"]),
            "trend_reversal_code": text_column(gw["POLLTRER_P"]),
            "exemption_type": text_column(gw["EX_CHE_TYP"]),
            "exemption_reason": text_column(gw["EX_CHE_PR"]),
            "exception_code": text_column(gw["POLLEXNTC"]),
            "level_set": text_column(gw["LEVELSET"]),
            "level_value": pd.to_numeric(gw["LEVELVALUE"], errors="coerce"),
            "level_unit": text_column(gw["LEVELUNIT"]),
            "inserted_at": pd.to_datetime(gw["INS_WHEN"], errors="coerce").dt.date,
            "inserted_by": gw["INS_BY"],
            "river_basin_code": text_column(gw["RBD_CD"]),
            "metadata_reference": gw["METADATA"],
        }
    )

    surface = pd.DataFrame(
        {
            "surface_water_body_id": text_column(ow["EU_CD_LS"]).map(lake_ids),
            "groundwater_body_id": None,
            "pollutant_id": text_column(ow["PSCODE"]).map(pollutant_ids),
            "failed_standard": ow["PS_FAIL"].map({"Y": True, "N": False}),
            "fail_reason_code": text_column(ow["FAILRBSP"]),
            "fail_reason_other": text_column(ow["FAILRBS_OT"]),
            "exemption_type": text_column(ow["CHEMEXTYPE"]),
            "exemption_reason": text_column(ow["CHEMEXPRE"]),
            "exception_code": text_column(ow["PS_EXCTYPE"]),
            "inserted_at": pd.to_datetime(ow["INS_WHEN"], errors="coerce").dt.date,
            "inserted_by": ow["INS_BY"],
            "river_basin_code": text_column(ow["RBD_CD"]),
            "metadata_reference": ow["METADATA"],
        }
    )

    status = pd.concat([ground, surface], ignore_index=True)
    status = status.dropna(subset=["pollutant_id"])
    status = status[
        status.surface_water_body_id.notna() | status.groundwater_body_id.notna()
    ]

    return update_db(
        status,
        engine,
        "water_body_chemical_status",
        ["surface_water_body_id", "groundwater_body_id", "pollutant_id", "inserted_at"],
    )


def load_protected_areas():
    engine = conn_db()
    definitions = [
        ("ffh.shp", "EU_CD_PH"),
        ("waterprotection.shp", "EU_CD_PD"),
        ("recreation_areas.shp", "EU_CD_PR"),
    ]

    frames = []
    for filename, id_column in definitions:
        source = gpd.read_file(data / filename).to_crs(25833)
        geometry = source.geometry.map(as_multipolygon)

        # Some source files do not contain LEGIS_NAME. Keep the target column
        # but fill it with None when absent.
        legislation_name = (
            source["LEGIS_NAME"] if "LEGIS_NAME" in source.columns else None
        )

        frame = gpd.GeoDataFrame(
            {
                "protected_area_id": source[id_column],
                "name": source["NAME"],
                "area_type": source["PROT_TYPE"],
                "legislation_code": source["LEG_CD"],
                "legislation_name": legislation_name,
                "legislation_url": source["URL"],
            },
            geometry=geometry,
            crs=source.crs,
        )
        frames.append(frame)

    areas = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=25833)
    update_db(areas, engine, "protected_area", ["protected_area_id"], geo=True)

    source = pd.read_csv(data / "protection_area_bewertung.csv", low_memory=False)
    valid_water = set(
        pd.read_sql(f"SELECT eu_cd_lw FROM {SCHEMA}.water_body", engine)["eu_cd_lw"]
    )
    valid_areas = set(
        pd.read_sql(f"SELECT protected_area_id FROM {SCHEMA}.protected_area", engine)["protected_area_id"]
    )

    relations = pd.DataFrame(
        {
            "water_body_id": source["EU_CD_WB"],
            "protected_area_id": source["EU_CD_P"],
            "relation_method": "source_reference",
            "evolution_type": source["EVOLUTIONT"],
            "exemption_code": source["PAREA_EX"],
            "comment": source["PA_COMMENT"],
        }
    )
    relations = relations[
        relations.water_body_id.isin(valid_water)
        & relations.protected_area_id.isin(valid_areas)
    ]

    update_db(
        relations,
        engine,
        "water_body_protected_area",
        ["water_body_id", "protected_area_id", "relation_method"],
    )
    return areas, relations


def load_spatial_relations():
    engine = conn_db()
    # Ensure relation tables exist even if this function is called on its own.
    initialize_database(engine)

    statements = [
        f'''INSERT INTO {SCHEMA}.station_groundwater_body
        (station_id, groundwater_body_id, relation_type, determination_method)
        SELECT s.station_id, g.eu_cd_gb, 'located_in', 'spatial_join'
        FROM {SCHEMA}.monitoring_station s
        JOIN {SCHEMA}.groundwater_body g
          ON s.location IS NOT NULL AND ST_Intersects(s.location, g.geometry)
        ON CONFLICT (station_id, groundwater_body_id) DO NOTHING''',

        f'''INSERT INTO {SCHEMA}.water_body_groundwater_body
        (water_body_id, groundwater_body_id, relation_type, overlap_area_km2,
         water_body_overlap_ratio, determination_method)
        SELECT w.eu_cd_lw, g.eu_cd_gb, 'overlaps',
               ST_Area(ST_Intersection(w.geometry,g.geometry))/1000000.0,
               ST_Area(ST_Intersection(w.geometry,g.geometry))/NULLIF(ST_Area(w.geometry),0),
               'spatial_join'
        FROM {SCHEMA}.water_body w
        JOIN {SCHEMA}.groundwater_body g
          ON ST_Intersects(w.geometry,g.geometry)
        ON CONFLICT (water_body_id, groundwater_body_id) DO NOTHING''',

        f'''INSERT INTO {SCHEMA}.catchment_groundwater_body
        (catchment_id, groundwater_body_id, relation_type, overlap_area_km2,
         catchment_overlap_ratio, determination_method)
        SELECT c.catchment_id, g.eu_cd_gb, 'overlaps',
               ST_Area(ST_Intersection(c.geometry,g.geometry))/1000000.0,
               ST_Area(ST_Intersection(c.geometry,g.geometry))/NULLIF(ST_Area(c.geometry),0),
               'spatial_join'
        FROM {SCHEMA}.catchment c
        JOIN {SCHEMA}.groundwater_body g
          ON ST_Intersects(c.geometry,g.geometry)
        ON CONFLICT (catchment_id, groundwater_body_id) DO NOTHING''',

        f'''INSERT INTO {SCHEMA}.station_catchment
        (station_id, catchment_id, relation_type, determination_method)
        SELECT s.station_id, c.catchment_id, 'located_in', 'spatial_join'
        FROM {SCHEMA}.monitoring_station s
        JOIN {SCHEMA}.catchment c
          ON s.location IS NOT NULL AND ST_Intersects(s.location, c.geometry)
        ON CONFLICT (station_id, catchment_id) DO NOTHING''',
    ]

    with engine.begin() as connection:
        for statement in statements:
            connection.exec_driver_sql(statement)
    print("Raeumliche Beziehungen wurden aktualisiert.")


# -----------------------------------------------------------------------------
# Full import sequence
# -----------------------------------------------------------------------------

def run_import():
    load_raw_sources()
    engine = initialize_database()

    gdf = gpd.read_file(data / "lakewaterbody.shp")
    stammdaten = pd.read_csv(data / "stammdaten.csv")
    gwb = gpd.read_file(data / "groundwaterbody.shp")

    load_waterbody(gdf, stammdaten)
    load_groundwaterbody(gwb)

    catchment_source = gpd.read_file(data / "catchments.shp").to_crs(25833)
    load_catchments(catchment_source)

    station_source = gpd.read_file(data / "messstellen.shp")
    load_monitoring_stations(station_source)

    bio = pd.read_csv(data / "einzeldaten.csv", low_memory=False)
    chem = pd.read_csv(data / "einzeldaten_chemie.csv", low_memory=False)
    load_parameters(bio, chem)

    section_source = pd.read_csv(data / "einzeldaten_abschnitte.csv", low_memory=False)
    load_sampling_sections(section_source)
    load_measurements(bio, chem)

    assessment_source = pd.read_csv(data / "messstellen_bewertung.csv", low_memory=False)
    load_assessments(assessment_source)

    gw = pd.read_csv(data / "GW_measures.csv", low_memory=False)
    ow = pd.read_csv(data / "OW_measures.csv", low_memory=False)
    load_chemical_status(gw, ow)

    load_protected_areas()
    load_spatial_relations()

    return engine


if __name__ == "__main__":
    run_import()

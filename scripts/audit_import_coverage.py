"""Inventory source files and compare them with the PostgreSQL water_kg schema.

The script is read-only. It is intended to make omissions in the import pipeline
visible before changing either the normalized schema or the knowledge graph.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from sqlalchemy import create_engine, inspect, text


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DATA = ROOT / "dummy_data"


def ensure_excel_reader() -> None:
    """Use openpyxl from the environment or the Codex document runtime."""
    try:
        import openpyxl  # noqa: F401
        return
    except ModuleNotFoundError:
        candidates = list(
            (Path.home() / ".cache" / "codex-runtimes").glob(
                "*/dependencies/python/Lib/site-packages"
            )
        )
        for candidate in candidates:
            sys.path.append(str(candidate))
            try:
                import openpyxl  # noqa: F401
                return
            except ModuleNotFoundError:
                continue
        raise RuntimeError("Excel-Dateien benötigen openpyxl.")


def frame_summary(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "rows": int(len(frame)),
        "columns": [str(column) for column in frame.columns],
        "non_null": {str(column): int(frame[column].notna().sum()) for column in frame.columns},
    }


def source_inventory() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for path in sorted(DATA.iterdir(), key=lambda item: item.name.casefold()):
        suffix = path.suffix.casefold()
        if suffix == ".csv":
            result[path.name] = {"kind": "csv", **frame_summary(pd.read_csv(path, low_memory=False))}
        elif suffix == ".shp":
            frame = gpd.read_file(path)
            result[path.name] = {
                "kind": "shapefile",
                "crs": str(frame.crs),
                **frame_summary(frame),
            }
        elif suffix == ".xlsx":
            ensure_excel_reader()
            workbook = pd.ExcelFile(path)
            result[path.name] = {
                "kind": "excel",
                "sheets": {
                    sheet: frame_summary(pd.read_excel(workbook, sheet_name=sheet))
                    for sheet in workbook.sheet_names
                },
            }
    return result


def database_inventory(schema: str = "water_kg") -> dict[str, Any]:
    password = os.getenv("SOLVE_DB_PASSWORD", "postgres")
    engine = create_engine(
        f"postgresql+psycopg://postgres:{password}@localhost:5432/SOLVE",
        pool_pre_ping=True,
    )
    inspector = inspect(engine)
    result: dict[str, Any] = {}
    with engine.connect() as connection:
        for table in sorted(inspector.get_table_names(schema=schema)):
            count = connection.execute(
                text(f'SELECT count(*) FROM "{schema}"."{table}"')
            ).scalar_one()
            columns = inspector.get_columns(table, schema=schema)
            result[table] = {
                "rows": int(count),
                "columns": [column["name"] for column in columns],
            }
    engine.dispose()
    return result


def quality_checks() -> dict[str, Any]:
    password = os.getenv("SOLVE_DB_PASSWORD", "postgres")
    engine = create_engine(
        f"postgresql+psycopg://postgres:{password}@localhost:5432/SOLVE",
        pool_pre_ping=True,
    )
    sql_checks = {
        "measurement_duplicate_natural_keys": """
            SELECT count(*) FROM (
              SELECT water_body_id, source_station_code, parameter_id, measured_at,
                     sampling_section, value, count(*)
              FROM water_kg.measurement
              GROUP BY 1,2,3,4,5,6 HAVING count(*) > 1
            ) duplicate_groups
        """,
        "measurement_rows_in_duplicate_groups": """
            SELECT coalesce(sum(row_count), 0) FROM (
              SELECT count(*) AS row_count
              FROM water_kg.measurement
              GROUP BY water_body_id, source_station_code, parameter_id, measured_at,
                       sampling_section, value HAVING count(*) > 1
            ) duplicate_groups
        """,
        "measurement_missing_station": "SELECT count(*) FROM water_kg.measurement WHERE station_id IS NULL",
        "sampling_section_missing_station": "SELECT count(*) FROM water_kg.sampling_section WHERE station_id IS NULL",
        "station_assessment_missing_station": "SELECT count(*) FROM water_kg.station_assessment WHERE station_id IS NULL",
        "chemical_status_surface": "SELECT count(*) FROM water_kg.water_body_chemical_status WHERE surface_water_body_id IS NOT NULL",
        "chemical_status_groundwater": "SELECT count(*) FROM water_kg.water_body_chemical_status WHERE groundwater_body_id IS NOT NULL",
        "foreign_key_constraints": """
            SELECT count(*) FROM information_schema.table_constraints
            WHERE table_schema = 'water_kg' AND constraint_type = 'FOREIGN KEY'
        """,
    }
    result: dict[str, Any] = {}
    with engine.connect() as connection:
        for name, query in sql_checks.items():
            result[name] = int(connection.execute(text(query)).scalar_one())
    engine.dispose()

    station_source = pd.read_csv(DATA / "messstellen_bewertung.csv", low_memory=False)
    station_key = ["EU_CD_LW", "MESSSTELLE", "PARAMETER", "JAHR", "METHODE"]
    result["station_assessment_source_rows"] = int(len(station_source))
    result["station_assessment_distinct_current_keys"] = int(
        len(station_source.drop_duplicates(subset=station_key))
    )

    bio = pd.read_csv(DATA / "einzeldaten.csv", low_memory=False)
    chem = pd.read_csv(DATA / "einzeldaten_chemie.csv", low_memory=False)
    result["measurement_source_rows"] = int(len(bio) + len(chem))
    result["chemical_status_source_rows"] = int(
        len(pd.read_csv(DATA / "GW_measures.csv"))
        + len(pd.read_csv(DATA / "OW_measures.csv"))
    )
    return result


def main() -> None:
    print(
        json.dumps(
            {
                "sources": source_inventory(),
                "database": database_inventory("water_kg"),
                "raw_database": database_inventory("water_raw"),
                "quality_checks": quality_checks(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

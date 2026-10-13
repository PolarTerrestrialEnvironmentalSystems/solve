"""Lossless ingestion of every data source in ``dummy_data`` into PostgreSQL.

The normalized ``water_kg`` schema intentionally contains only modeled domain
concepts.  This module adds a separate ``water_raw`` landing schema so that no
source row or source column is discarded while the domain model evolves.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from sqlalchemy import create_engine, inspect, text


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "dummy_data"
RAW_SCHEMA = "water_raw"


def ensure_excel_reader() -> None:
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


def database_engine():
    password = os.getenv("SOLVE_DB_PASSWORD", "postgres")
    url = os.getenv(
        "SOLVE_DATABASE_URL",
        f"postgresql+psycopg://postgres:{password}@localhost:5432/SOLVE",
    )
    return create_engine(url, pool_pre_ping=True)


def database_name(value: Any) -> str:
    name = str(value).strip().casefold()
    name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    if not name:
        return "source_index"
    if name[0].isdigit():
        name = f"column_{name}"
    return name[:63]


def unique_database_columns(columns: list[Any]) -> tuple[list[str], list[dict[str, Any]]]:
    used: dict[str, int] = {}
    names: list[str] = []
    metadata: list[dict[str, Any]] = []
    for ordinal, source_name in enumerate(columns, start=1):
        base = database_name(source_name)
        used[base] = used.get(base, 0) + 1
        target = base if used[base] == 1 else f"{base}_{used[base]}"
        names.append(target)
        metadata.append(
            {
                "ordinal_position": ordinal,
                "source_column": str(source_name),
                "database_column": target,
            }
        )
    return names, metadata


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def long_name_lookup() -> dict[str, str]:
    ensure_excel_reader()
    dictionary = pd.read_excel(DATA / "attribute_names.xlsx")
    dictionary = dictionary.dropna(subset=["shortname", "Longname"])
    lookup: dict[str, str] = {}
    for row in dictionary.itertuples(index=False):
        lookup.setdefault(str(row.shortname).casefold(), str(row.Longname))
    return lookup


def inferred_long_name(source_column: str, lookup: dict[str, str]) -> str | None:
    exact = lookup.get(source_column.casefold())
    if exact:
        return exact
    # Shapefile/DBF attribute names are limited to ten characters and several
    # source fields are visibly truncated (for example DRAIN_C / DRAIN_CD).
    candidates = {
        value
        for key, value in lookup.items()
        if len(source_column) >= 6
        and (key.startswith(source_column.casefold()) or source_column.casefold().startswith(key))
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def prepare_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frame = frame.copy()
    names, metadata = unique_database_columns(list(frame.columns))
    frame.columns = names
    frame.insert(0, "source_row_number", range(1, len(frame) + 1))
    return frame, metadata


def table_name(path: Path, sheet: str | None = None) -> str:
    base = database_name(path.stem)
    if sheet is not None:
        base = f"{base}__{database_name(sheet)}"
    return base[:63]


def load_raw_sources(engine=None) -> dict[str, int]:
    """Replace the reproducible raw landing tables and return their row counts."""
    engine = engine or database_engine()
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS postgis")
        connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{RAW_SCHEMA}"')

    attribute_lookup = long_name_lookup()
    loaded_at = datetime.now(timezone.utc)
    file_records: list[dict[str, Any]] = []
    column_records: list[dict[str, Any]] = []
    loaded_tables: dict[str, int] = {}

    data_files = {
        path.name: path
        for path in DATA.iterdir()
        if path.suffix.casefold() in {".csv", ".shp", ".xlsx"}
    }

    for filename, path in sorted(data_files.items(), key=lambda item: item[0].casefold()):
        suffix = path.suffix.casefold()
        frames: list[tuple[str, pd.DataFrame, bool, str | None]] = []
        if suffix == ".csv":
            frames.append((table_name(path), pd.read_csv(path, low_memory=False), False, None))
        elif suffix == ".shp":
            source = gpd.read_file(path)
            if source.crs is None:
                raise ValueError(f"{filename}: räumliche Quelle besitzt kein Koordinatensystem")
            frames.append((table_name(path), source.to_crs(25833), True, None))
        else:
            ensure_excel_reader()
            workbook = pd.ExcelFile(path)
            for sheet in workbook.sheet_names:
                frames.append(
                    (
                        table_name(path, None if len(workbook.sheet_names) == 1 else sheet),
                        pd.read_excel(workbook, sheet_name=sheet),
                        False,
                        sheet,
                    )
                )

        total_rows = 0
        targets: list[str] = []
        for target, source, geo, sheet in frames:
            prepared, columns = prepare_frame(source)
            if geo:
                prepared = gpd.GeoDataFrame(prepared, geometry="geometry", crs=25833)
                prepared.to_postgis(
                    target,
                    engine,
                    schema=RAW_SCHEMA,
                    if_exists="replace",
                    index=False,
                )
            else:
                prepared.to_sql(
                    target,
                    engine,
                    schema=RAW_SCHEMA,
                    if_exists="replace",
                    index=False,
                    # Keep multi-row INSERTs below PostgreSQL/psycopg's 65,535
                    # bind-parameter ceiling even for wide source tables.
                    chunksize=1000,
                    method="multi",
                )
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    f'ALTER TABLE "{RAW_SCHEMA}"."{target}" '
                    f'ADD PRIMARY KEY (source_row_number)'
                )
            for column in columns:
                column_records.append(
                    {
                        "source_file": filename,
                        "sheet_name": sheet,
                        "target_table": target,
                        **column,
                        "long_name": inferred_long_name(column["source_column"], attribute_lookup),
                    }
                )
            total_rows += len(prepared)
            targets.append(target)
            loaded_tables[target] = len(prepared)

        file_records.append(
            {
                "source_file": filename,
                "file_extension": suffix,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
                "logical_row_count": total_rows,
                "target_tables": ",".join(targets),
                "loaded_at_utc": loaded_at,
            }
        )

    # Register sidecars and documentation too. Their bytes are part of the
    # supplied dataset even though only .shp is the logical spatial table.
    registered = {record["source_file"] for record in file_records}
    for path in sorted(DATA.iterdir(), key=lambda item: item.name.casefold()):
        if path.is_file() and path.name not in registered:
            file_records.append(
                {
                    "source_file": path.name,
                    "file_extension": path.suffix.casefold(),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256(path),
                    "logical_row_count": None,
                    "target_tables": None,
                    "loaded_at_utc": loaded_at,
                }
            )

    pd.DataFrame(file_records).to_sql(
        "source_file",
        engine,
        schema=RAW_SCHEMA,
        if_exists="replace",
        index=False,
    )
    pd.DataFrame(column_records).to_sql(
        "source_column",
        engine,
        schema=RAW_SCHEMA,
        if_exists="replace",
        index=False,
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f'ALTER TABLE "{RAW_SCHEMA}"."source_file" ADD PRIMARY KEY (source_file)'
        )
        connection.exec_driver_sql(
            f'CREATE UNIQUE INDEX uq_source_column ON "{RAW_SCHEMA}"."source_column" '
            f'(source_file, COALESCE(sheet_name, \'\'), ordinal_position)'
        )

    loaded_tables["source_file"] = len(file_records)
    loaded_tables["source_column"] = len(column_records)
    engine.dispose()
    return loaded_tables


def validate_raw_sources(engine=None) -> list[str]:
    """Return validation errors; an empty list means row/column coverage is exact."""
    engine = engine or database_engine()
    errors: list[str] = []
    inspector = inspect(engine)
    with engine.connect() as connection:
        registry = pd.read_sql_table("source_file", connection, schema=RAW_SCHEMA)
        columns = pd.read_sql_table("source_column", connection, schema=RAW_SCHEMA)
        for record in registry.dropna(subset=["target_tables"]).itertuples(index=False):
            targets = str(record.target_tables).split(",")
            actual_rows = sum(
                int(
                    connection.execute(
                        text(f'SELECT count(*) FROM "{RAW_SCHEMA}"."{target}"')
                    ).scalar_one()
                )
                for target in targets
            )
            if actual_rows != int(record.logical_row_count):
                errors.append(
                    f"{record.source_file}: {actual_rows} DB rows != {int(record.logical_row_count)} source rows"
                )
        for target, expected in columns.groupby("target_table").size().items():
            actual = len(inspector.get_columns(target, schema=RAW_SCHEMA)) - 1
            if actual != int(expected):
                errors.append(f"{target}: {actual} DB columns != {int(expected)} source columns")
    engine.dispose()
    return errors


if __name__ == "__main__":
    counts = load_raw_sources()
    for name, count in sorted(counts.items()):
        print(f"{RAW_SCHEMA}.{name}: {count} rows")
    validation_errors = validate_raw_sources()
    if validation_errors:
        raise RuntimeError("\n".join(validation_errors))
    print("Raw-data validation: OK")

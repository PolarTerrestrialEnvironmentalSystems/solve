"""Import a verified version-1 bundle into a NEW immutable PostgreSQL schema.

No existing schema is dropped or replaced. A byte-identical bundle is a no-op
only after all stored input columns, metadata and row counts match the input.
Database extension/schema creation and data insertion are one transaction.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parent
CONTRACT = json.loads((ROOT / 'CONTRACT.json').read_text(encoding='utf-8'))
TABLES = CONTRACT['tables']
JSON_COLUMNS = frozenset(CONTRACT['json_columns'])
REFERENCES = {
    'raw_record': {'dataset_id':'dataset'},
    'water_body': {'dataset_id':'dataset','raw_id':'raw_record'},
    'water_body_identifier': {'water_body_id':'water_body'},
    'monitoring_station': {'dataset_id':'dataset','raw_id':'raw_record'},
    'station_water_body': {'station_id':'monitoring_station','water_body_id':'water_body','raw_id':'raw_record'},
    'water_body_assessment': {'water_body_id':'water_body','raw_id':'raw_record'},
    'lake_water_body': {'lake_id':'lake','water_body_id':'water_body'},
    'match_candidate': {'lake_id':'lake','water_body_id':'water_body'},
    'event': {'lake_id':'lake'},
    'event_evidence': {'event_id':'event','source_id':'source_document'},
}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def numeric_canonical(value: Any) -> str:
    """Exact JSON-number normalization for round-trip integrity hashes.

    PostgreSQL JSONB may return 1e30 as an integer and 1.0 as a decimal. The
    serialized JSON numeric value is unchanged. Normalize decimal tokens, not
    binary floats: Decimal(str(float)) represents the token sent by json.dumps.
    Digits/exponent are manipulated directly, so Decimal's context precision
    cannot round a large integer. The original bundle byte hash is independent.
    """
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError('Non-finite JSON number')
        sign, digits, exponent = number.as_tuple()
        if not any(digits):
            return '0'
        digits = list(digits)
        while digits[-1] == 0:
            digits.pop()
            exponent += 1
        coefficient = str(digits[0])
        if len(digits) > 1:
            coefficient += '.' + ''.join(str(digit) for digit in digits[1:])
        return ('-' if sign else '') + coefficient + 'e' + str(exponent + len(digits) - 1)
    if isinstance(value, list):
        return '[' + ','.join(numeric_canonical(item) for item in value) + ']'
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError('JSON object keys must be strings')
        return '{' + ','.join(json.dumps(key, ensure_ascii=False) + ':' + numeric_canonical(value[key]) for key in sorted(value)) + '}'
    raise TypeError(f'Unsupported JSON value type: {type(value).__name__}')


def load_env(path: str | Path | None) -> None:
    if path is not None:
        from dotenv import load_dotenv
        file = Path(path).resolve()
        if not file.is_file():
            raise ValueError(f'Environment file not found: {file}')
        load_dotenv(file, override=False)


def validate_schema_name(value: str) -> str:
    if not re.fullmatch(r'solve33_[a-z0-9_]{1,55}', value):
        raise ValueError('Schema must begin solve33_ and use only lower-case letters, numbers or underscores (max 63 characters).')
    return value


def table_digest(rows: list[dict], pk: str) -> str:
    return hashlib.sha256(numeric_canonical(sorted(rows, key=lambda r: r[pk])).encode('utf-8')).hexdigest()


def validate_geometry(value: Any, location: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or 'crs' in value:
        raise ValueError(f'{location}: geometry must be GeoJSON already transformed to WGS84, without a CRS member')
    if value.get('type') not in {'Point','MultiPoint','LineString','MultiLineString','Polygon','MultiPolygon','GeometryCollection'}:
        raise ValueError(f'{location}: unsupported GeoJSON geometry type')
    if value['type'] == 'GeometryCollection':
        geometries = value.get('geometries')
        if not isinstance(geometries, list) or not geometries:
            raise ValueError(f'{location}: empty geometry collection')
        for geometry in geometries:
            validate_geometry(geometry, location)
        return

    def visit(coordinates):
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError(f'{location}: empty/invalid coordinates')
        if isinstance(coordinates[0], (int, float)):
            if len(coordinates) != 2 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in coordinates):
                raise ValueError(f'{location}: exactly two finite coordinates required')
            lon, lat = coordinates
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError(f'{location}: coordinates are outside WGS84 bounds; transform source CRS first')
        else:
            for child in coordinates:
                visit(child)
    visit(value.get('coordinates'))


def validate_bundle(bundle: dict) -> dict[str, int]:
    if not isinstance(bundle, dict) or bundle.get('bundle_version') != 1:
        raise ValueError('Expected bundle_version = 1')
    tables = bundle.get('tables')
    if not isinstance(tables, dict) or set(tables) != set(TABLES):
        raise ValueError('Bundle must contain exactly the CONTRACT.json table names, with empty arrays for unavailable data')
    counts = {}
    for table, spec in TABLES.items():
        rows = tables[table]
        if not isinstance(rows, list):
            raise ValueError(f'{table}: expected row list')
        keys = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(spec['columns']):
                raise ValueError(f'{table}: columns do not exactly match CONTRACT.json')
            pk = row[spec['pk']]
            if not isinstance(pk, str) or not pk or pk in keys:
                raise ValueError(f'{table}: empty/non-string/duplicate primary key')
            keys.add(pk)
            for column, value in row.items():
                if column not in JSON_COLUMNS and value is not None and not isinstance(value, str):
                    raise ValueError(f'{table}.{column}: expected string or null; original structure belongs in JSON payload')
            if 'geometry' in row:
                validate_geometry(row['geometry'], f'{table}/{pk}')
            canonical(row)  # reject NaN and non-JSON objects
        counts[table] = len(rows)
    keys_by_table = {table: {row[spec['pk']] for row in tables[table]} for table, spec in TABLES.items()}
    for table, references in REFERENCES.items():
        for row in tables[table]:
            for column, target in references.items():
                if row[column] is not None and row[column] not in keys_by_table[target]:
                    raise ValueError(f'{table}.{column}: unresolved foreign key to {target}; use a candidate/issue instead of an invented relation')
    raw_by_id = {r['raw_id']: r for r in tables['raw_record']}
    for table in ['water_body','monitoring_station']:
        for row in tables[table]:
            if row['raw_id'] is not None and raw_by_id[row['raw_id']]['dataset_id'] != row['dataset_id']:
                raise ValueError(f'{table}: raw provenance points to a different dataset')
    for row in tables['match_candidate']:
        if row['status'] not in {'accepted','review','unmatched','rejected'}:
            raise ValueError('match_candidate: unsupported review status')
        if row['status'] in {'accepted','review'} and row['water_body_id'] is None:
            raise ValueError('match_candidate: accepted/review candidate requires a real water body')
    for row in tables['lake_water_body']:
        if row['relation_type'] not in {'same_water_body','part_of'}:
            raise ValueError('lake_water_body: relation_type must explicitly distinguish identity from part_of')
    for row in tables['import_issue']:
        if row['severity'] not in {'info','warning','error'}:
            raise ValueError('import_issue: unsupported severity')
    return counts


def read_bundle(path: str | Path) -> tuple[dict, str, dict[str, int], dict[str, str]]:
    contents = Path(path).read_bytes()
    bundle = json.loads(contents.decode('utf-8-sig'), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f'Invalid JSON number {x}')))
    counts = validate_bundle(bundle)
    digest = hashlib.sha256(contents).hexdigest()
    hashes = {table: table_digest(bundle['tables'][table], spec['pk']) for table, spec in TABLES.items()}
    return bundle, digest, counts, hashes


def verify_stored(connection, schema: str, digest: str, counts: dict, hashes: dict) -> None:
    from psycopg import sql
    from psycopg.rows import dict_row
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(sql.SQL('SELECT bundle_sha256,bundle_version,counts,table_sha256 FROM {}._import_run').format(sql.Identifier(schema)))
        runs = cursor.fetchall()
        if len(runs) != 1 or runs[0] != {'bundle_sha256': digest, 'bundle_version': 1, 'counts': counts, 'table_sha256': hashes}:
            raise ValueError('Existing schema metadata differs from input bundle; choose a fresh schema. Nothing was changed.')
        for table, spec in TABLES.items():
            cursor.execute(sql.SQL('SELECT {} FROM {}.{} ORDER BY {}').format(
                sql.SQL(',').join(sql.Identifier(c) for c in spec['columns']),
                sql.Identifier(schema), sql.Identifier(table), sql.Identifier(spec['pk'])))
            rows = cursor.fetchall()
            if len(rows) != counts[table] or table_digest(rows, spec['pk']) != hashes[table]:
                raise ValueError(f'Existing schema has modified/incomplete contents in {table}; refusing to overwrite.')
        cursor.execute('SELECT table_name FROM information_schema.views WHERE table_schema=%s', (schema,))
        actual_views = {row['table_name'] for row in cursor.fetchall()}
        expected_views = {'water_body_history','station_history','lake_reporting_history','event_water_body_context'}
        if not expected_views <= actual_views:
            raise ValueError('Existing schema lacks the required temporal views; choose a fresh schema. No existing schema was modified.')
        cursor.execute('SELECT count(*) AS n FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname=%s AND NOT c.convalidated', (schema,))
        if cursor.fetchone()['n']:
            raise ValueError('Unvalidated constraints found in imported schema')


def import_bundle(bundle_path: str | Path, database_url: str, schema: str | None = None) -> dict:
    import psycopg
    from psycopg import sql
    from psycopg.types.json import Jsonb
    bundle, digest, counts, hashes = read_bundle(bundle_path)
    schema = validate_schema_name(schema or f'solve33_{digest[:16]}')
    database_url = database_url.replace('postgresql+psycopg://', 'postgresql://', 1)
    with psycopg.connect(database_url, connect_timeout=20) as connection:
        with connection.cursor() as cursor:
            # Serializes concurrent attempts against exactly this schema. The
            # transaction-level lock is released on commit AND rollback.
            cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (schema,))
            cursor.execute('SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s)', (schema,))
            exists = cursor.fetchone()[0]
            if exists:
                verify_stored(connection, schema, digest, counts, hashes)
                return {'status':'unchanged_verified','schema':schema,'bundle_sha256':digest,'counts':counts}
            cursor.execute('CREATE EXTENSION IF NOT EXISTS postgis')
            cursor.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            ddl = (ROOT / 'schema.sql').read_text(encoding='utf-8').replace('@SCHEMA@', sql.Identifier(schema).as_string(connection))
            cursor.execute(ddl)
            for table, spec in TABLES.items():
                columns = spec['columns']
                statement = sql.SQL('INSERT INTO {}.{} ({}) VALUES ({})').format(
                    sql.Identifier(schema), sql.Identifier(table),
                    sql.SQL(',').join(sql.Identifier(c) for c in columns),
                    sql.SQL(',').join(sql.Placeholder() for _ in columns))
                rows = [[Jsonb(row[c]) if c in JSON_COLUMNS and row[c] is not None else row[c] for c in columns]
                        for row in bundle['tables'][table]]
                if rows:
                    cursor.executemany(statement, rows)
            cursor.execute(sql.SQL('INSERT INTO {}._import_run(bundle_sha256,bundle_version,counts,table_sha256) VALUES (%s,1,%s,%s)').format(sql.Identifier(schema)),
                           (digest, Jsonb(counts), Jsonb(hashes)))
            verify_stored(connection, schema, digest, counts, hashes)
        # The context manager commits only after successful verification.
    return {'status':'created_verified','schema':schema,'bundle_sha256':digest,'counts':counts}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--schema', help='Optional fresh solve33_... schema; default derived from bundle SHA-256')
    parser.add_argument('--env-file')
    parser.add_argument('--validate-only', action='store_true', help='Validate bundle without connecting or changing a database')
    args = parser.parse_args(argv)
    load_env(args.env_file)
    if args.validate_only:
        _, digest, counts, _ = read_bundle(args.bundle)
        schema = validate_schema_name(args.schema or f'solve33_{digest[:16]}')
        print(json.dumps({'status':'bundle_validated','schema':schema,'bundle_sha256':digest,'counts':counts}, ensure_ascii=False, indent=2))
        return 0
    database_url = os.getenv('SOLVE_DATABASE_URL')
    if not database_url:
        parser.error('SOLVE_DATABASE_URL fehlt; über --env-file oder Prozessumgebung setzen.')
    result = import_bundle(args.bundle, database_url, args.schema)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

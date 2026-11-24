"""Opt-in integration tests against an explicitly named isolated test database.

Required environment variables:
  SOLVE33_TEST_DATABASE_URL  PostgreSQL connection string; dbname MUST begin
                            solve33_test (checked before connecting and in SQL).
  SOLVE33_TEST_BUNDLE        Absolute path to the actual prepared data bundle.

No production/default connection configuration is read. Tests create and clean
only uniquely generated solve33_it_* schemas inside the explicit test database.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pg_import


class DatabaseIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.getenv('SOLVE33_TEST_DATABASE_URL')
        bundle_path = os.getenv('SOLVE33_TEST_BUNDLE')
        if not url or not bundle_path:
            raise unittest.SkipTest('Set both SOLVE33_TEST_DATABASE_URL and SOLVE33_TEST_BUNDLE to opt in; no database was contacted.')
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict
        from psycopg.types.json import Jsonb
        cls.psycopg = psycopg
        cls.sql = sql
        cls.Jsonb = Jsonb
        cls.database_url = url.replace('postgresql+psycopg://', 'postgresql://', 1)
        # Crucially this guard runs BEFORE connecting and does not fall back to
        # the PostgreSQL user name, PGDATABASE or the application's .env.
        expected_database = conninfo_to_dict(cls.database_url).get('dbname', '')
        if not expected_database.startswith('solve33_test'):
            raise RuntimeError('Integration tests require an explicit dbname beginning solve33_test; refusing connection.')
        cls.bundle_path = Path(bundle_path).resolve()
        cls.bundle, cls.bundle_sha, cls.counts, cls.hashes = pg_import.read_bundle(cls.bundle_path)
        if not cls.counts['event'] or not cls.counts['water_body'] or not cls.counts['event_evidence']:
            raise RuntimeError('Integration tests require the actual nonempty research bundle, including events, water bodies and evidence.')
        with psycopg.connect(cls.database_url, connect_timeout=10) as connection:
            actual_database = connection.execute('SELECT current_database()').fetchone()[0]
            if actual_database != expected_database or not actual_database.startswith('solve33_test'):
                raise RuntimeError('Connected database does not match the explicitly selected isolated test database.')

    def setUp(self):
        self.schemas = []
        self.temp = tempfile.TemporaryDirectory(prefix='solve33-integration-')

    def tearDown(self):
        try:
            with self.psycopg.connect(self.database_url, connect_timeout=10) as connection:
                database = connection.execute('SELECT current_database()').fetchone()[0]
                if not database.startswith('solve33_test'):
                    raise RuntimeError('Cleanup guard failed; no schemas were removed.')
                for schema in self.schemas:
                    if not schema.startswith('solve33_it_'):
                        raise RuntimeError('Cleanup guard failed; unexpected schema prefix.')
                    connection.execute(self.sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(self.sql.Identifier(schema)))
        finally:
            self.temp.cleanup()

    def fresh_schema(self):
        schema = 'solve33_it_' + uuid.uuid4().hex
        self.schemas.append(schema)
        return schema

    def import_actual(self):
        schema = self.fresh_schema()
        result = pg_import.import_bundle(self.bundle_path, self.database_url, schema)
        self.assertEqual(result['status'], 'created_verified')
        self.assertEqual(result['counts'], self.counts)
        self.assertEqual(result['bundle_sha256'], self.bundle_sha)
        return schema

    def changed_bundle(self, mutate):
        bundle = copy.deepcopy(self.bundle)
        mutate(bundle)
        path = Path(self.temp.name) / (uuid.uuid4().hex + '.json')
        path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
        return path

    def schema_exists(self, schema):
        with self.psycopg.connect(self.database_url) as connection:
            return connection.execute('SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s)', (schema,)).fetchone()[0]

    def test_actual_bundle_counts_foreign_keys_and_idempotence(self):
        schema = self.import_actual()
        second = pg_import.import_bundle(self.bundle_path, self.database_url, schema)
        self.assertEqual(second['status'], 'unchanged_verified')
        self.assertEqual(second['bundle_sha256'], self.bundle_sha)
        with self.psycopg.connect(self.database_url) as connection:
            for table, expected in self.counts.items():
                actual = connection.execute(self.sql.SQL('SELECT count(*) FROM {}.{}').format(self.sql.Identifier(schema), self.sql.Identifier(table))).fetchone()[0]
                self.assertEqual(actual, expected, table)
            constraints = connection.execute('SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname=%s AND c.contype=%s AND c.convalidated', (schema, 'f')).fetchone()[0]
            self.assertGreaterEqual(constraints, 18)
            geometries = connection.execute(self.sql.SQL('SELECT count(*) FROM {}.water_body WHERE geom IS NOT NULL AND (ST_SRID(geom)<>4326 OR NOT ST_IsValid(geom))').format(self.sql.Identifier(schema))).fetchone()[0]
            self.assertEqual(geometries, 0)
            datasets = {r['dataset_id']:r for r in self.bundle['tables']['dataset']}
            for view, table, id_column in [('water_body_history','water_body','water_body_id'),('station_history','monitoring_station','station_id')]:
                history = connection.execute(self.sql.SQL('SELECT {},dataset_id,reporting_cycle,report_year,retrieved_at FROM {}.{}').format(
                    self.sql.Identifier(id_column),self.sql.Identifier(schema),self.sql.Identifier(view))).fetchall()
                expected = set()
                for row in self.bundle['tables'][table]:
                    dataset = datasets[row['dataset_id']]
                    raw_year = str(dataset['metadata'].get('report_year',''))
                    report_year = int(raw_year) if len(raw_year)==4 and raw_year.isdigit() else None
                    expected.add((row[id_column],row['dataset_id'],dataset['reporting_cycle'],report_year,dataset['retrieved_at']))
                self.assertEqual(set(history), expected)
                self.assertEqual(len(history), self.counts[table])
            accepted = connection.execute(self.sql.SQL('SELECT count(*) FROM {}.lake_reporting_history').format(self.sql.Identifier(schema))).fetchone()[0]
            self.assertEqual(accepted, self.counts['lake_water_body'])

    def test_database_foreign_key_rejects_invented_event_lake(self):
        schema = self.import_actual()
        event_id = self.bundle['tables']['event'][0]['event_id']
        with self.assertRaises(self.psycopg.errors.ForeignKeyViolation):
            with self.psycopg.connect(self.database_url) as connection:
                connection.execute(self.sql.SQL('UPDATE {}.event SET lake_id=%s WHERE event_id=%s').format(self.sql.Identifier(schema)),
                                   ('nonexistent-' + uuid.uuid4().hex, event_id))
        result = pg_import.import_bundle(self.bundle_path, self.database_url, schema)
        self.assertEqual(result['status'], 'unchanged_verified')

    def test_invalid_polygon_rejected_and_new_schema_rolled_back(self):
        schema = self.fresh_schema()
        # Bow-tie coordinates are within WGS84, but the polygon is topologically
        # invalid. This intentionally exercises PostGIS rather than Python bounds.
        def mutate(bundle):
            bundle['tables']['water_body'][0]['geometry'] = {
                'type':'Polygon',
                'coordinates':[[[11,51],[12,52],[11,52],[12,51],[11,51]]]
            }
        path = self.changed_bundle(mutate)
        with self.assertRaises(self.psycopg.errors.CheckViolation):
            pg_import.import_bundle(path, self.database_url, schema)
        self.assertFalse(self.schema_exists(schema), 'Failed initial import must not leave a partial schema')

    def test_database_geometry_bounds_reject_projected_coordinates(self):
        schema = self.import_actual()
        body_id = self.bundle['tables']['water_body'][0]['water_body_id']
        with self.assertRaises(self.psycopg.errors.CheckViolation):
            with self.psycopg.connect(self.database_url) as connection:
                connection.execute(self.sql.SQL('UPDATE {}.water_body SET geometry=%s WHERE water_body_id=%s').format(self.sql.Identifier(schema)),
                                   (self.Jsonb({'type':'Point','coordinates':[401463,5887612]}), body_id))
        result = pg_import.import_bundle(self.bundle_path, self.database_url, schema)
        self.assertEqual(result['status'], 'unchanged_verified')

    def test_changed_bundle_cannot_replace_existing_snapshot(self):
        schema = self.import_actual()
        def mutate(bundle):
            bundle['tables']['event'][0]['statement'] += ' Integration test change.'
        path = self.changed_bundle(mutate)
        with self.assertRaisesRegex(ValueError, 'metadata differs'):
            pg_import.import_bundle(path, self.database_url, schema)
        result = pg_import.import_bundle(self.bundle_path, self.database_url, schema)
        self.assertEqual(result['status'], 'unchanged_verified')

    def test_missing_existing_row_is_detected_without_repairing_or_overwriting(self):
        schema = self.import_actual()
        evidence_id = self.bundle['tables']['event_evidence'][0]['evidence_id']
        with self.psycopg.connect(self.database_url) as connection:
            connection.execute(self.sql.SQL('DELETE FROM {}.event_evidence WHERE evidence_id=%s').format(self.sql.Identifier(schema)), (evidence_id,))
        with self.assertRaisesRegex(ValueError, 'modified/incomplete'):
            pg_import.import_bundle(self.bundle_path, self.database_url, schema)
        with self.psycopg.connect(self.database_url) as connection:
            remaining = connection.execute(self.sql.SQL('SELECT count(*) FROM {}.event_evidence').format(self.sql.Identifier(schema))).fetchone()[0]
            self.assertEqual(remaining, self.counts['event_evidence'] - 1)

    def test_unmanaged_existing_schema_is_not_adopted(self):
        schema = self.fresh_schema()
        with self.psycopg.connect(self.database_url) as connection:
            connection.execute(self.sql.SQL('CREATE SCHEMA {}').format(self.sql.Identifier(schema)))
            connection.execute(self.sql.SQL('CREATE TABLE {}.sentinel(value text)').format(self.sql.Identifier(schema)))
            connection.execute(self.sql.SQL('INSERT INTO {}.sentinel VALUES (%s)').format(self.sql.Identifier(schema)), ('preserve this table',))
        with self.assertRaises(self.psycopg.errors.UndefinedTable):
            pg_import.import_bundle(self.bundle_path, self.database_url, schema)
        with self.psycopg.connect(self.database_url) as connection:
            self.assertEqual(connection.execute(self.sql.SQL('SELECT value FROM {}.sentinel').format(self.sql.Identifier(schema))).fetchall(), [('preserve this table',)])


if __name__ == '__main__':
    unittest.main()

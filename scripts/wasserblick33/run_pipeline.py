"""Validate offline, or import PostgreSQL then the temporal Neo4j projection."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import sys
import time
from common import save_json, canonical_json
import pg_import
import neo4j_import


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=Path('/data/prepared/bundle.json'))
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--apply', action='store_true', help='Create new PostgreSQL snapshot and its Neo4j graph.')
    parser.add_argument('--report', type=Path, help='Optional writable report path; no passwords are written.')
    args = parser.parse_args()
    pg_import.load_env(args.env_file)
    bundle, bundle_hash, counts, _ = pg_import.read_bundle(args.bundle)
    if not args.apply:
        graph = neo4j_import.build_graph(bundle['tables'], neo4j_import.snapshot_id(bundle['tables']))
        result = {'status': 'offline_validated', 'bundle_sha256': bundle_hash, 'tables': counts,
                  'graph_nodes': len(graph['nodes']), 'graph_relationships': len(graph['edges']),
                  'graph_sha256': graph['graph_sha256']}
    else:
        required = ['SOLVE_DATABASE_URL', 'NEO4J_URI', 'NEO4J_USER', 'NEO4J_PASSWORD', 'NEO4J_DATABASE']
        missing = [n for n in required if not os.environ.get(n)]
        if missing:
            raise ValueError('Missing environment variables: ' + ', '.join(missing))
        postgres_result = pg_import.import_bundle(args.bundle, os.environ['SOLVE_DATABASE_URL'])
        tables = neo4j_import.read_postgres(os.environ['SOLVE_DATABASE_URL'], postgres_result['schema'])
        graph = neo4j_import.build_graph(tables, neo4j_import.snapshot_id(tables))
        # Neo4j startup may take longer than PostgreSQL healthcheck. Only wait on
        # connectivity failures; never mask authentication or validation errors.
        from neo4j import GraphDatabase
        from neo4j.exceptions import ServiceUnavailable
        deadline = time.monotonic() + 120
        while True:
            try:
                with GraphDatabase.driver(os.environ['NEO4J_URI'],
                        auth=(os.environ['NEO4J_USER'], os.environ['NEO4J_PASSWORD']),
                        connection_timeout=5) as driver:
                    driver.verify_connectivity()
                break
            except ServiceUnavailable:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(3)
        graph_result = neo4j_import.apply_graph(graph, *(os.environ[k] for k in required[1:]))
        result = {'status': 'imported_and_verified', 'postgres': postgres_result, 'neo4j': graph_result}
    if args.report:
        save_json(args.report, result)
    print(canonical_json(result))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, FileNotFoundError, ImportError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        print(f'Pipeline failed ({type(error).__name__}). Check server status and private configuration; no connection secrets are logged.', file=sys.stderr)
        sys.exit(1)

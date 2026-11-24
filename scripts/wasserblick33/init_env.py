"""Create new local credentials. Existing files are never overwritten."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets


def create_env(path: Path) -> Path:
    path = path.resolve()
    postgres_password = secrets.token_hex(32)
    neo4j_password = secrets.token_hex(32)
    content = (
        '# Generated locally; keep private and do not commit.\n'
        f'POSTGRES_PASSWORD={postgres_password}\n'
        f'NEO4J_PASSWORD={neo4j_password}\n'
        f'SOLVE_DATABASE_URL=postgresql://solve33:{postgres_password}@127.0.0.1:55433/solve33\n'
        'NEO4J_URI=bolt://127.0.0.1:7688\n'
        'NEO4J_USER=neo4j\n'
        'NEO4J_DATABASE=neo4j\n'
        'SOLVE33_DATA_DIR=./data\n'
    )
    # O_EXCL closes the race between checking existence and writing credentials.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(content)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(Path(__file__).resolve().parent / '.env'))
    args = parser.parse_args(argv)
    try:
        path = create_env(Path(args.output))
    except FileExistsError:
        parser.exit(1, 'Environment file already exists; it was not changed.\n')
    print(f'Created credential file: {path}')
    print('Passwords were generated without being printed. Existing database credentials are not reset by this script.')


if __name__ == '__main__':
    main()

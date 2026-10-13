"""Verify distributed files without dependencies or network access."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
expected = json.loads((root/'RELEASE_SHA256.json').read_text(encoding='utf-8'))
bad = [name for name, sha in expected.items()
       if not (root/name).is_file() or hashlib.sha256((root/name).read_bytes()).hexdigest() != sha]
if bad:
    raise SystemExit('Dateien fehlen oder wurden verändert: '+', '.join(bad))
print(f'{len(expected)} Paketdateien geprüft: unverändert.')

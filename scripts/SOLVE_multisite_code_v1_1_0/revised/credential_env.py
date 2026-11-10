"""Load only the explicitly configured API keys; never execute a dotenv file."""
import os
from pathlib import Path


def load(cfg):
    filename = cfg.get('credentials_env_file')
    values = {}
    if filename:
        from dotenv import dotenv_values
        path = Path(filename)
        if not path.is_file():
            raise ValueError('Konfigurierte credentials_env_file wurde nicht gefunden')
        values = dotenv_values(path, encoding='utf-8-sig', interpolate=False)
    for name in ('SERPER_KEY', 'BLABLADOR_KEY', 'GRAPHRAG_API_KEY2'):
        if not os.getenv(name) and values.get(name):
            os.environ[name] = values[name]
    # An explicit canonical key from either source wins over the legacy alias.
    # This also supports shell-only configuration without any dotenv file.
    if not os.getenv('BLABLADOR_KEY'):
        fallback = os.getenv('GRAPHRAG_API_KEY') or values.get('GRAPHRAG_API_KEY')
        if fallback:
            os.environ['BLABLADOR_KEY'] = fallback

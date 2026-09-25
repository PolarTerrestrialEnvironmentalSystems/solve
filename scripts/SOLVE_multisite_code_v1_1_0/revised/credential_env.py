"""Load only the explicitly configured API keys; never execute a dotenv file."""
import os
from pathlib import Path


def load(cfg):
    filename = cfg.get('credentials_env_file')
    if not filename:
        return
    from dotenv import dotenv_values
    path = Path(filename)
    if not path.is_file():
        raise ValueError('Konfigurierte credentials_env_file wurde nicht gefunden')
    values = dotenv_values(path, encoding='utf-8-sig', interpolate=False)
    for name in ('SERPER_KEY', 'BLABLADOR_KEY'):
        if not os.getenv(name) and values.get(name):
            os.environ[name] = values[name]

"""Fine-ontology PDF batch runner. Paths are relative to this distribution."""
from __future__ import annotations

import argparse
import copy
import importlib.metadata
import platform
import re
import sys
from pathlib import Path

import run_parsing_tests as runner
from blablador_client import Settings, minute_report, Clock
from common import BASE_DIR, RunLock, digest, file_hash, load_env, read_json, read_jsonl, write_json, write_jsonl, utc_now


def local(value):
    p = Path(value).expanduser()
    return (p if p.is_absolute() else BASE_DIR / p).resolve()


def runtime_versions():
    versions = {}
    for line in (BASE_DIR / 'requirements.lock.txt').read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        name, version = line.split('==')
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            raise ValueError(f'{name} fehlt. requirements.lock.txt installieren.') from None
        if installed != version:
            raise ValueError(f'{name}: installiert {installed}, benötigt {version}. requirements.lock.txt installieren.')
        versions[name] = installed
    return {'python': platform.python_version(), 'packages': versions}


def discover(directory, recursive):
    if not directory.is_dir():
        raise ValueError('Eingabeordner existiert nicht.')
    paths = directory.rglob('*') if recursive else directory.iterdir()
    inventory = []
    for p in sorted((p for p in paths if p.is_file() and p.suffix.lower() == '.pdf'),
                    key=lambda p: p.relative_to(directory).as_posix()):
        relative = p.relative_to(directory).as_posix()
        if not p.resolve().is_relative_to(directory):
            raise ValueError('PDF-Verknüpfung zeigt außerhalb des Eingabeordners.')
        sha = file_hash(p)
        stem = re.sub(r'[^A-Za-z0-9_-]', '_', p.stem)[:50] or 'document'
        inventory.append({'relative_path': relative, 'sha256': sha,
                          'document_key': stem + '_' + digest({'path': relative, 'sha': sha})[:16]})
    if not inventory:
        raise ValueError('Keine PDF-Dateien im Eingabeordner gefunden.')
    return inventory


def aggregate(output, batch):
    logs = []
    events, relationships = [], []
    for item in batch['documents']:
        folder = output / item['document_key']
        logs.extend(read_jsonl(folder/'logs/api_requests.jsonl', tolerate_partial_tail=True))
        if item['status'] in {'complete', 'complete_with_failures'}:
            events.extend(read_jsonl(folder/'fein/events.jsonl'))
            relationships.extend(read_jsonl(folder/'fein/relationships.jsonl'))
    if logs:
        write_jsonl(output/'logs/api_requests.jsonl', logs)
        batch['api_stats'] = minute_report(output/'logs/api_requests.jsonl',
                                          output/'logs/api_requests_per_minute.csv', Clock().now())
    if not batch['config']['dry_run']:
        write_jsonl(output/'combined/events.jsonl', events)
        write_jsonl(output/'combined/relationships.jsonl', relationships)
    batch['totals'] = {'events': len(events), 'relationships': len(relationships)}
    batch['updated_at'] = utc_now()
    write_json(output/'batch_manifest.json', batch)


def main(argv=None):
    p = argparse.ArgumentParser(description='Alle PDFs mit ausschließlich feiner Ontologie parsen.')
    p.add_argument('--input', default='input', help='Eingabeordner, relativ zum Paket oder absolut.')
    p.add_argument('--output', default='results/batch', help='Neuer Batch-Ausgabeordner.')
    p.add_argument('--recursive', action='store_true', help='Auch PDFs in Unterordnern.')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--dry-run', action='store_true', help='PDF-Aufbereitung ohne API-Aufrufe.')
    p.add_argument('--env-file', default='.env')
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--rpm', type=int, default=30)
    p.add_argument('--timeout', type=float, default=600)
    p.add_argument('--max-output-tokens', type=int, default=16000)
    p.add_argument('--thinking-mode', choices=['off', 'on', 'auto'], default='off')
    p.add_argument('--model', help='Konkrete Modell-ID; sonst BLABLADOR_MODEL.')
    p.add_argument('--chunk-chars', type=int, default=4000, help='Weiche Zielgröße; vollständige Absätze/Tabellenzeilen erhalten.')
    p.add_argument('--max-context-calls', type=int, default=10)
    args = p.parse_args(argv)
    if not 1 <= args.workers <= 8 or not 1 <= args.rpm <= 30:
        raise ValueError('workers: 1–8; rpm: 1–30 (gemeinsames Limit).')
    if args.chunk_chars < 1000 or args.max_context_calls < 0:
        raise ValueError('chunk-chars mindestens 1000; max-context-calls darf nicht negativ sein.')
    load_env(local(args.env_file))
    settings = Settings.from_env()
    for name in ['rpm','timeout','max_output_tokens','thinking_mode']:
        setattr(settings,name,getattr(args,name))
    if args.model:
        settings.model = args.model
    settings.validate(require_key=not args.dry_run)
    directory, output = local(args.input), local(args.output)
    if output == directory or output.is_relative_to(directory):
        raise ValueError('Ausgabeordner muss außerhalb des Eingabeordners liegen.')
    inventory = discover(directory, args.recursive)
    versions = runtime_versions()
    config = {'settings':settings.public(), 'workers':args.workers, 'recursive':args.recursive,
              'dry_run':args.dry_run, 'chunk_chars':args.chunk_chars,
              'max_context_calls':args.max_context_calls, 'input_directory':str(directory)}
    fingerprints = {x.name:file_hash(x) for x in sorted(BASE_DIR.glob('*.py'))}
    for name in ['ontologie_fein.json','Event_headers.xlsx','relationship_headers.xlsx','requirements.lock.txt']:
        fingerprints[name] = file_hash(BASE_DIR/name)
    signature = digest({'inventory':inventory,'config':config,'runtime':versions,'files':fingerprints})
    with RunLock(BASE_DIR/'results/.parser.lock'):
        path = output/'batch_manifest.json'
        if args.resume:
            if not path.exists():
                raise ValueError('Kein batch_manifest.json für --resume gefunden.')
            batch = read_json(path)
            if batch['signature'] != signature:
                raise ValueError('PDF-Bestand, Code, Ontologie, Python, Pakete oder Einstellungen geändert. Neuen Ausgabeordner wählen.')
        else:
            if output.exists() and any(output.iterdir()):
                raise ValueError('Ausgabeordner nicht leer. --resume oder neuen Ordner verwenden.')
            batch = {'signature':signature, 'created_at':utc_now(), 'config':config, 'runtime':versions,
                     'file_hashes':fingerprints, 'model_identity':None,
                     'documents':[{**x,'status':'pending'} for x in inventory]}
        output.mkdir(parents=True,exist_ok=True)
        batch['status'] = 'running'
        write_json(path,batch)
        try:
            for item in batch['documents']:
                if item['status'] == ('dry_run' if args.dry_run else 'complete'):
                    for relative, sha in item.get('output_hashes',{}).items():
                        artifact = output/item['document_key']/relative
                        if not artifact.is_file() or file_hash(artifact) != sha:
                            raise ValueError(f'Gespeichertes Ergebnis fehlt oder wurde geändert: {relative}')
                    continue
                dest = output/item['document_key']
                doc_args = runner.parser().parse_args([])
                doc_args.pdf = str(directory/item['relative_path'])
                doc_args.dry_run = args.dry_run
                doc_args.resume = (dest/'manifest.json').exists()
                doc_args.workers = args.workers
                doc_args.chunk_chars = args.chunk_chars
                doc_args.max_context_calls = args.max_context_calls
                dest.mkdir(parents=True,exist_ok=True)
                item['status'] = 'running'
                write_json(path,batch)
                print(f"PDF: {item['relative_path']}",flush=True)
                try:
                    runner.run_experiment(doc_args,copy.deepcopy(settings),dest,item['document_key'],
                                          expected_model_identity=batch['model_identity'])
                finally:
                    if (dest/'manifest.json').exists():
                        doc = read_json(dest/'manifest.json')
                        item['status'] = doc['status']
                        if doc['status'] == 'complete':
                            item['output_hashes'] = {name:file_hash(dest/name) for name in
                                ['fein/events.jsonl','fein/relationships.jsonl','fein/events.xlsx',
                                 'fein/relationships.xlsx','fein/review_items.jsonl','fein/summary.json']}
                        identity = doc.get('model_identity')
                        if identity and batch['model_identity'] is None:
                            batch['model_identity'] = identity
                    else:
                        item['status'] = 'interrupted_or_failed'
                    aggregate(output,batch)
            batch['status'] = 'dry_run' if args.dry_run else ('complete' if all(x['status']=='complete' for x in batch['documents']) else 'complete_with_failures')
        except BaseException:
            batch['status'] = 'interrupted_or_failed'
            raise
        finally:
            aggregate(output,batch)
        print(f"Batch {batch['status']}: {output}",flush=True)
        return 2 if batch['status']=='complete_with_failures' else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('Unterbrochen; mit identischem Befehl und --resume fortsetzen.',file=sys.stderr)
        raise SystemExit(130)
    except (ValueError,RuntimeError,OSError) as exc:
        print(f'Fehler: {exc}',file=sys.stderr)
        raise SystemExit(1)

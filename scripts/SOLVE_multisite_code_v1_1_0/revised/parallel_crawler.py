"""Bounded process parallelism with one SQLite writer per persistent lake group."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import closing

import run_web_enrichment as core

PROJECT = Path(__file__).resolve().parent.parent


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def split_config(cfg, count):
    if not 1 <= count <= len(cfg['waterbodies']):
        raise ValueError('Gruppenzahl muss zwischen 1 und Gewässerzahl liegen')
    result = []
    for index in range(count):
        part = copy.deepcopy(cfg)
        part.pop('credentials_env_file', None)
        part['waterbodies'] = cfg['waterbodies'][index::count]
        ids = {wb['id'] for wb in part['waterbodies']}
        part['scientific_sources'] = [s for s in cfg['scientific_sources'] if s['waterbody_id'] in ids]
        # These budgets apply to the whole run, not independently to every process.
        for section, key in [('limits', 'max_fetches_per_run'),
                             ('limits', 'max_searches_per_run'),
                             ('limits', 'max_tasks_per_run'),
                             ('limits', 'max_urls_per_host_per_run'),
                             ('preflight', 'max_calls_per_run'),
                             ('review', 'max_calls_per_run')]:
            total = cfg[section][key]
            part[section][key] = total // count + (index < total % count)
        if part['preflight']['max_calls_per_run'] < 1:
            raise ValueError('Vorprüfungsbudget ist kleiner als die Gruppenzahl')
        # Requests across processes may overlap. Scale each process's search spacing.
        part['search']['delay_seconds'] = cfg['search'].get('delay_seconds', 2) * count
        result.append(core.load_config(part))
    return result


def prepare(root, cfg, groups, source=None):
    """Import a stopped dossier once; never change or overwrite its database."""
    root = Path(root).resolve()
    source = Path(source).resolve() if source else None
    parts = split_config(cfg, groups)
    if source and (root.is_relative_to(source) or source.is_relative_to(root)):
        raise ValueError('Quell- und Zieldossier müssen getrennte Ordner sein')
    if root.exists():
        raise ValueError('Ziel existiert bereits. Zum Fortsetzen run verwenden; neues Ziel für prepare wählen.')
    if source and not (source / 'research.sqlite').is_file():
        raise ValueError('Im Quelldossier fehlt research.sqlite')
    root.mkdir(parents=True)
    # A manifest is written last: incomplete preparation cannot accidentally run.
    with core.WorkspaceLock(root):
        if source:
            with core.WorkspaceLock(source):
                with closing(sqlite3.connect(source / 'research.sqlite')) as old:
                    profiles = {row[0] for row in old.execute('SELECT id FROM profiles')}
                    expected = {core.stable(dict(waterbody=wb, topics=cfg['topics'],
                                                 context=cfg.get('context', '')))
                                for wb in cfg['waterbodies']}
                    if not expected.issubset(profiles):
                        raise ValueError('Konfiguration passt nicht zu den Gewässerprofilen im Quelldossier')
                    with closing(sqlite3.connect(root / 'checkpoint.sqlite')) as snapshot:
                        old.backup(snapshot)
                        if snapshot.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                            raise ValueError('Integritätsprüfung der kopierten Datenbank fehlgeschlagen')
                for folder in ('archive', 'metadata', 'extracted'):
                    if (source / folder).exists():
                        shutil.copytree(source / folder, root / 'checkpoint' / folder)
        entries = []
        for index, part in enumerate(parts, 1):
            name = f'group-{index:02d}'
            target = root / name
            target.mkdir()
            if source:
                shutil.copy2(root / 'checkpoint.sqlite', target / 'research.sqlite')
                for folder in ('archive', 'metadata', 'extracted'):
                    if (root / 'checkpoint' / folder).exists():
                        shutil.copytree(root / 'checkpoint' / folder, target / folder)
            write_json(target / 'config.json', part)
            entries.append({'directory': name, 'waterbodies': [w['id'] for w in part['waterbodies']]})
        write_json(root / 'parallel.json', {'version': 1, 'groups': entries,
                                         'imported_checkpoint': bool(source)})
    print(f'Vorbereitet: {len(entries)} Gruppen unter {root}', flush=True)


def worker(root, name, env_file):
    folder = root / name
    cfg = core.load_config(folder / 'config.json')
    cfg['credentials_env_file'] = str(Path(env_file).resolve()) if env_file else ''
    cfg['storage']['shared_cache_dir'] = str(root / 'download_cache')
    cfg['storage']['shared_cache_wait_seconds'] = 600
    os.environ['SOLVE_BLABLADOR_CHAT_URLS'] = json.dumps(sorted({
        os.getenv(cfg[section]['base_url_env'], cfg[section]['base_url']).rstrip('/') + '/chat/completions'
        for section in ('preflight', 'review') if cfg[section]['api_key_env'] == 'BLABLADOR_KEY'
    }))
    # Stop between tasks, never halfway through committing/exporting results.
    research = None
    stop_requested = False
    import llm_gate
    llm_gate.stop_requested = lambda: stop_requested
    def stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True
        if research is not None:
            research.parallel_stop_requested = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, stop)
    with core.WorkspaceLock(folder):
        research = core.Research(cfg, folder)
        research.parallel_stop_requested = stop_requested
        try:
            summary = research.run()
            write_json(folder / 'last_run.json', summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            return 130 if summary.get('stop_reason') == 'interrupted' else 0
        finally:
            research.close()


def run(root, workers, env_file=None, llm_workers=2, llm_interval=2, llm_cooldown=60):
    if workers < 1:
        raise ValueError('--workers muss mindestens 1 sein')
    if llm_workers < 1 or llm_interval < 0 or llm_cooldown < 1:
        raise ValueError('LLM-Slots und Wartezeiten sind ungültig')
    manifest = json.loads((root / 'parallel.json').read_text(encoding='utf-8'))
    pending = list(manifest['groups'])
    # Check keys before spawning any workers; secrets remain in the environment.
    import credential_env
    credential_env.load({'credentials_env_file': env_file or ''})
    for entry in pending:
        cfg = core.load_config(root / entry['directory'] / 'config.json')
        needed = []
        if cfg['search']['provider'] == 'serper':
            needed.append(cfg['search']['api_key_env'])
        if cfg['preflight']['enabled']:
            needed.append(cfg['preflight']['api_key_env'])
        if cfg['review']['mode'] == 'api' or cfg['review']['required']:
            needed.append(cfg['review']['api_key_env'])
        for key in needed:
            if not os.getenv(key):
                raise ValueError('API-Schlüssel fehlt: ' + key + ' (mit --env-file laden)')
    if os.getenv('BLABLADOR_KEY'):
        key_count = len({os.getenv(name) for name in ('BLABLADOR_KEY', 'GRAPHRAG_API_KEY2') if os.getenv(name)})
        print(f'Blablador: {key_count} Schlüssel geladen; maximal {llm_workers} gleichzeitige LLM-Aufrufe insgesamt.', flush=True)
    active, completed = {}, []
    stopping = False
    (root / 'logs').mkdir(exist_ok=True)
    previous = signal.getsignal(signal.SIGTERM)
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    try:
        with core.WorkspaceLock(root):
            while pending or active:
                try:
                    while pending and len(active) < workers and not stopping:
                        entry = pending.pop(0)
                        name = entry['directory']
                        log = (root / 'logs' / (name + '.log')).open('a', encoding='utf-8')
                        log.write('\n--- Neuer Lauf ' + time.strftime('%Y-%m-%d %H:%M:%S') + ' ---\n')
                        log.flush()
                        command = [sys.executable, '-u', str(Path(__file__).resolve()), '_worker',
                                   '--workspace', str(root), '--group', name]
                        options = {'start_new_session': True} if os.name != 'nt' else {
                            'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
                        try:
                            environment = dict(os.environ, PYTHONIOENCODING='utf-8')
                            environment.update(SOLVE_LLM_GATE_DIR=str(Path.home() / '.cache/solve/llm-gate'),
                                               SOLVE_LLM_SLOTS=str(llm_workers),
                                               SOLVE_LLM_INTERVAL=str(llm_interval),
                                               SOLVE_LLM_COOLDOWN=str(llm_cooldown))
                            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                                       env=environment, **options)
                        except BaseException:
                            log.close()
                            raise
                        active[name] = (process, log)
                        print(f'{name} gestartet (PID {process.pid}); Log: {root / "logs" / (name + ".log")}', flush=True)
                    for name, (process, log) in list(active.items()):
                        code = process.poll()
                        if code is not None:
                            log.close()
                            del active[name]
                            completed.append({'group': name, 'exit_code': code})
                            print(f'{name} beendet: Exit-Code {code}', flush=True)
                    if active:
                        time.sleep(.25)
                except KeyboardInterrupt:
                    if not stopping:
                        stopping = True
                        pending.clear()
                        print('Abbruch angefordert. Warte auf Speichern und Export der Worker …', flush=True)
                        for process, log in active.values():
                            if process.poll() is None:
                                try:
                                    process.send_signal(signal.CTRL_BREAK_EVENT if os.name == 'nt' else signal.SIGINT)
                                except ProcessLookupError:
                                    pass
                    else:
                        print('Worker speichern noch. Bitte auf das Ende warten.', flush=True)
            write_json(root / 'parallel_last_run.json', {'interrupted': stopping, 'workers': completed})
    finally:
        signal.signal(signal.SIGTERM, previous)
        # Also clean up children if the coordinator itself encounters an exception.
        for process, log in active.values():
            if process.poll() is None:
                process.terminate()
            process.wait()
            log.close()
    return 130 if stopping else int(any(e['exit_code'] != 0 for e in completed))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'run', '_worker'])
    parser.add_argument('--config', default=str(PROJECT / 'inputs/sites_blablador_33_serper_v1_6_2.json'))
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--source-workspace', help='Gestopptes altes Dossier einmalig übernehmen')
    parser.add_argument('--groups', type=int, default=30, help='Feste Gruppen beim Vorbereiten (Standard: 30)')
    parser.add_argument('--workers', type=int, default=30, help='Gleichzeitig laufende Gruppen (Standard: 30)')
    parser.add_argument('--env-file', default=os.getenv('SOLVE_ENV_FILE'))
    parser.add_argument('--group')
    parser.add_argument('--llm-workers', type=int, default=2, help='Gleichzeitige LLM-Aufrufe aller Worker zusammen')
    parser.add_argument('--llm-interval', type=float, default=2, help='Mindestabstand zwischen LLM-Anfragestarts in Sekunden')
    parser.add_argument('--llm-cooldown', type=float, default=60, help='Mindestpause nach HTTP 429; steigt bei Wiederholung')
    args = parser.parse_args(argv)
    root = Path(args.workspace).resolve()
    if args.command == 'prepare':
        prepare(root, core.load_config(args.config), args.groups, args.source_workspace)
        return 0
    if args.command == '_worker':
        return worker(root, args.group, args.env_file)
    return run(root, args.workers, args.env_file, args.llm_workers, args.llm_interval, args.llm_cooldown)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

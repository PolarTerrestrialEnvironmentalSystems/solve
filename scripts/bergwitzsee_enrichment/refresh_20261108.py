"""Rebuild Bergwitzsee from preserved LHW exports and refreshed official supplements.

Does not modify the older PostgreSQL databases or Neo4j Desktop.
Run with the project's outputs/wasserblick33_20261031/runtime/venv/Scripts/python.exe.
"""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'outputs/bergwitzsee_postgres_kg_20261108'
OLD = ROOT / 'outputs/bergwitzsee_ergaenzung_20261102'
BASE = ROOT / 'outputs/bergwitzsee_neo4j_v2_events_20261101'
DBNAME = 'solve_bergwitzsee_v2_20261108'
sys.path.insert(0, str(ROOT/'scripts/bergwitzsee_v2'))

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

def acquire():
    if (OUT/'runtime/postgres.env').exists():
        raise RuntimeError('Sources already imported; use a new output directory for another acquisition')
    mon = OUT/'data/monitoring'
    mon.mkdir(parents=True, exist_ok=True)
    source = json.loads((OLD/'data/monitoring/monitoring_supplement.json').read_text(encoding='utf-8'))
    records = []
    for item in source['sources']:
        request = urllib.request.Request(item['url'], headers={'User-Agent':'SOLVE-Bergwitzsee-research/1.0'})
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            record = dict(item, retrieved_date='2026-11-08', retrieved_at=datetime.now(timezone.utc).isoformat(),
                          final_url=response.url, status=response.status, bytes=len(body),
                          sha256=hashlib.sha256(body).hexdigest())
        if item['file'].endswith('.pdf'):
            assert body.startswith(b'%PDF'), 'Not a PDF'
            assert record['sha256'] == item['sha256'], 'PDF changed; manual extraction must be rechecked'
        (mon/item['file']).write_bytes(body)
        record['unchanged_bytes'] = record['sha256'] == item['sha256']
        records.append(record)
        print('Downloaded', item['file'], len(body), flush=True)
    for name in ['ms_profile_download.json', 'uba_download.json']:
        shutil.copy2(OLD/'data/monitoring'/name, mon/name)
    prep = module('refresh_monitoring', ROOT/'scripts/bergwitzsee_enrichment/prepare_monitoring_supplement.py')
    previous = sys.argv
    try:
        sys.argv = [str(prep.__file__), '--directory', str(mon)]
        prep.main()
    finally:
        sys.argv = previous
    report = json.loads((mon/'monitoring_supplement.json').read_text(encoding='utf-8'))
    for item in report['sources']:
        actual = next(r for r in records if r['file'] == item['file'])
        item.update(retrieved_date='2026-11-08', retrieved_at=actual['retrieved_at'])
    save(mon/'monitoring_supplement.json', report)
    save(mon/'download_manifest.json', records)
    # Preserve the original portal retrieval date. Session-bound ZIP URLs are
    # not represented as newly downloaded or necessarily current measurements.
    lhw = ROOT/'outputs/bergwitzsee_lhw_20261101'
    manifest = json.loads((lhw/'manifest.json').read_text(encoding='utf-8'))
    verified = []
    for archive in manifest['archives']:
        p = lhw/archive['file']
        assert hashlib.sha256(p.read_bytes()).hexdigest() == archive['sha256']
        target = OUT/'data/lhw'/archive['file']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        verified.append(archive)
    save(OUT/'data/lhw/manifest.json', manifest)
    save(OUT/'LHW_REUSE.json', dict(original_retrieval_date=manifest['retrieval_date'],
         original_portal=manifest['portal'], source_exports_verified=len(verified),
         newly_downloaded=False, reason='Preserved complete original portal ZIP exports; session-bound download URLs.'))
    geo = module('refresh_geo', ROOT/'scripts/bergwitzsee_enrichment/acquire_geodata.py')
    geo.OUT = OUT/'data/geodata'
    geo.main()
    validator = module('refresh_geo_validation', ROOT/'scripts/bergwitzsee_enrichment/validate_geodata.py')
    validator.OUT = geo.OUT
    validator.main()

def postgres():
    builder = module('refresh_pg_base', ROOT/'scripts/bergwitzsee_v2/build_postgres.py')
    builder.OUT = OUT
    builder.DBNAME = DBNAME
    builder.main()
    shutil.copy2(OUT/'POSTGRES_VALIDATION.json', OUT/'POSTGRES_BASE_IMPORT.json')
    supplement = module('refresh_pg_supplement', ROOT/'scripts/bergwitzsee_enrichment/build_postgres_supplement.py')
    supplement.OUT = OUT
    supplement.DBNAME = DBNAME
    supplement.BATCH = 'bergwitzsee_official_refresh_20261108'
    supplement.main()

def setup_graph():
    runtime = OUT/'runtime'
    preview = runtime/'neo4j_preview'
    shared = OLD/'runtime/neo4j_preview/neo4j-community-2026.10.1'
    state = json.loads((OLD/'runtime/neo4j_preview/preview-status.json').read_text(encoding='utf-8'))
    if preview.exists():
        raise RuntimeError('New graph runtime already exists; use its manager start command')
    for port in [7479, 7693]:
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(('127.0.0.1', port))
    preview.mkdir()
    conf = preview/'conf'
    conf.mkdir()
    short = Path.home()/'.solve-bergwitz-20261108-tmp'
    short.mkdir(exist_ok=True)
    password = secrets.token_hex(24)
    pg = module('refresh_private', ROOT/'outputs/wasserblick33_20261031/runtime/manage_test_pg.py')
    pg.private_write(preview/'preview.env', f'NEO4J_URI=bolt://127.0.0.1:7693\nNEO4J_USER=neo4j\nNEO4J_PASSWORD={password}\nNEO4J_DATABASE=neo4j\n')
    settings = {
        'server.directories.data':(preview/'data').as_posix(),
        'server.directories.transaction.logs.root':(preview/'transactions').as_posix(),
        'server.directories.logs':(preview/'logs').as_posix(),
        'server.directories.run':(preview/'run').as_posix(),
        'server.directories.import':(OUT/'import').as_posix(),
        'server.default_listen_address':'127.0.0.1',
        'server.default_advertised_address':'127.0.0.1',
        'server.http.enabled':'true','server.http.listen_address':'127.0.0.1:7479',
        'server.http.advertised_address':'127.0.0.1:7479',
        'server.https.enabled':'false','server.bolt.enabled':'true',
        'server.bolt.listen_address':'127.0.0.1:7693','server.bolt.advertised_address':'127.0.0.1:7693',
        'server.memory.heap.initial_size':'256m','server.memory.heap.max_size':'768m',
        'server.memory.pagecache.size':'256m','dbms.security.auth_enabled':'true',
        'dbms.usage_report.enabled':'false','dbms.fleet_manager.enabled':'false',
        'server.fleet_discovery.enabled':'false',
        'server.jvm.additional':f'-Djdk.net.unixdomain.tmpdir={short.as_posix()}' }
    (conf/'neo4j.conf').write_text('\n'.join(f'{k}={v}' for k,v in settings.items())+'\n',encoding='utf-8')
    manager = (OLD/'runtime/manage_neo4j_preview.py').read_text(encoding='utf-8')
    manager = manager.replace("HOME = PREVIEW / 'neo4j-community-2026.10.1'", f'HOME = Path({str(shared)!r})\nCONF = PREVIEW / "conf"')
    manager = manager.replace("(HOME / 'data/databases/system')", "(PREVIEW / 'data/databases/system')")
    manager = manager.replace("str(HOME / 'conf')", "str(CONF)")
    manager = manager.replace('7478','7479').replace('7692','7693').replace('.solve-bergwitz-enriched-neo4j-tmp','.solve-bergwitz-20261108-tmp')
    (runtime/'manage_neo4j_preview.py').write_text(manager,encoding='utf-8')
    env = os.environ.copy()
    env.update(JAVA_HOME=state['java_home'], NEO4J_HOME=str(shared), NEO4J_CONF=str(conf), TEMP=str(short), TMP=str(short))
    with (preview/'initial-password.log').open('wb') as log:
        subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(shared/'bin/neo4j-admin.ps1'),
                        'dbms','set-initial-password',password], env=env, cwd=shared, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW, check=True, timeout=90)
    state.update(home=str(shared), short_temp_dir=str(short), http='http://127.0.0.1:7479', bolt='bolt://127.0.0.1:7693',
                 env_file=str(preview/'preview.env'), launcher_pid=None, launcher_created=None,
                 data_directory=str(preview/'data'), created_at=datetime.now(timezone.utc).isoformat())
    save(preview/'preview-status.json', state)
    # manager.marker requires a system directory. It is created by first startup,
    # so remove only that existence check for this dedicated runtime manager.
    manager = manager.replace("if not ENVFILE.is_file() or not (PREVIEW / 'data/databases/system').is_dir():", "if not ENVFILE.is_file() or not CONF.is_dir():")
    (runtime/'manage_neo4j_preview.py').write_text(manager,encoding='utf-8')
    managed = module('refresh_manager', runtime/'manage_neo4j_preview.py')
    managed.start()

def graph():
    from dotenv import dotenv_values
    from sqlalchemy import create_engine, text
    from neo4j import GraphDatabase
    from import_extensions import import_extensions
    original = module('refresh_neo_importer', OUT/'source_scripts/neo4j_v2_import.py')
    verifier = module('refresh_verifier', ROOT/'scripts/dummy_v2/import_graph.py')
    pg = dotenv_values(OUT/'runtime/postgres.env')
    cfg = dotenv_values(OUT/'runtime/neo4j_preview/preview.env')
    engine = create_engine(pg['SOLVE_DATABASE_URL'])
    assert engine.url.database == DBNAME
    assert cfg['NEO4J_URI'] == 'bolt://127.0.0.1:7693'
    extension = json.loads((OUT/'BERGWITZSEE_EXTENSION.json').read_text(encoding='utf-8'))
    # Actual event/evidence/version rows come back out of PostgreSQL, not a
    # parallel unverified JSON import that could diverge from the database.
    with engine.connect() as conn:
        for table, target in [('event','event'),('event_evidence','event_evidence'),('source_document','source_document'),('water_body_version','water_body')]:
            loaded = list(conn.execute(text(f'SELECT payload FROM water_events.{table}')).scalars())
            assert sorted(map(lambda r:json.dumps(r,sort_keys=True), loaded)) == sorted(map(lambda r:json.dumps(r,sort_keys=True), extension['tables'][target]))
            extension['tables'][target] = loaded
        groundwater = [dict(r) for r in conn.execute(text('SELECT * FROM water_kg_extension.groundwater_body_version')).mappings()]
        pg_counts = {t:conn.execute(text(f'SELECT count(*) FROM water_kg_2.{t}')).scalar_one() for t in ['measurement','monitoring_station','water_body_assessment','parameter','unit','sampling_section']}
    with GraphDatabase.driver(cfg['NEO4J_URI'], auth=(cfg['NEO4J_USER'],cfg['NEO4J_PASSWORD'])) as driver:
        with driver.session(database='neo4j') as session:
            assert session.run('MATCH(n) RETURN count(n)').single()[0] == 0, 'Target graph must be empty'
        imp = original.WaterKgV2Importer(engine, driver, 'neo4j', batch_size=2000)
        imp.run(rebuild=False)
        properties = verifier.verify_properties(original, imp, driver)
        ext = import_extensions(driver, 'neo4j', extension)
        with driver.session(database='neo4j') as session:
            session.run('CREATE CONSTRAINT groundwater_version_id IF NOT EXISTS FOR (v:GroundwaterBodyVersion) REQUIRE v.version_id IS UNIQUE').consume()
            rows = [dict(id=r['version_id'], parent=r['groundwater_body_id'],
                         props=dict(original.clean_properties(r), valid_time_kind='reporting_year_not_observation_date')) for r in groundwater]
            result = session.run('UNWIND $rows AS row MATCH(w:GroundwaterBodyV2 {groundwater_body_id:row.parent}) '
                'CREATE(v:GroundwaterBodyVersion) SET v=row.props CREATE(w)-[r:HAS_VERSION]->(v) '
                'SET r.reporting_year=v.reporting_year,r.source_url=v.source_url RETURN count(v)',rows=rows).single()[0]
            assert result == len(groundwater) == 3
            for row in rows:
                props = session.run('MATCH(v:GroundwaterBodyVersion {version_id:$id}) RETURN properties(v)',id=row['id']).single()[0]
                assert props == row['props']
            counts = {r['label']:r['n'] for r in session.run('MATCH(n) UNWIND labels(n) AS label RETURN label,count(*) AS n')}
            nodes = session.run('MATCH(n) RETURN count(n)').single()[0]
            rels = session.run('MATCH()-[r]->() RETURN count(r)').single()[0]
            assert counts['Event'] == 73 and counts['MeasurementV2'] == pg_counts['measurement']
            assert session.run('MATCH(e:Event) WHERE NOT (:WaterBodyV2)-[:HAS_EVENT]->(e) RETURN count(e)').single()[0] == 0
            assert session.run('MATCH(m:MeasurementV2) WHERE m.comparison_operator<>"=" AND m.value IS NOT NULL RETURN count(m)').single()[0] == 0
            constraints = session.run('SHOW CONSTRAINTS YIELD name RETURN name ORDER BY name').value()
            assert len(constraints) == 44
        save(OUT/'NEO4J_VALIDATION.json',dict(status='passed', nodes=nodes, relationships=rels, counts_by_label=counts,
             property_checks=properties, postgres_counts=pg_counts, extension=ext,
             groundwater_versions_verified=3, constraints=constraints, original_schema_reused=True,
             events_read_back_from_postgres=True, censored_values_not_exact=True))
    engine.dispose()
    print(json.dumps(dict(nodes=nodes,relationships=rels,event_links=ext['direct_water_body_event_links'])),flush=True)

def export():
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url
    from sqlalchemy import create_engine, text
    delivery = OUT/'export'
    if delivery.exists():
        raise RuntimeError('Export already exists; refusing overwrite')
    delivery.mkdir()
    runtime = OUT/'runtime'
    manager = module('refresh_export_manager', runtime/'manage_neo4j_preview.py')
    state = manager.marker()
    shared = Path(state['home'])
    conf = runtime/'neo4j_preview/conf'
    check = runtime/'dump_restore_check'
    check.mkdir()
    env = os.environ.copy()
    env.update(JAVA_HOME=state['java_home'], NEO4J_HOME=str(shared), NEO4J_CONF=str(conf),
               TEMP=state['short_temp_dir'], TMP=state['short_temp_dir'])
    commands = []
    def admin(args, name):
        print(name, flush=True)
        with (check/name).open('wb') as log:
            p = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(shared/'bin/neo4j-admin.ps1'),*args],
                env=env,cwd=shared,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,timeout=600)
        commands.append(dict(args=args,exit_code=p.returncode))
        if p.returncode:
            raise RuntimeError('Neo4j admin failed: '+str(check/name))
    manager.stop()
    try:
        admin(['database','dump','neo4j',f'--to-path={delivery}','--overwrite-destination=false'],'dump.log')
        verify_conf = check/'conf'
        verify_conf.mkdir()
        data, txns = check/'data', check/'transactions'
        (verify_conf/'neo4j.conf').write_text('\n'.join([
            f'server.directories.data={data.as_posix()}',f'server.directories.transaction.logs.root={txns.as_posix()}',
            f'server.directories.logs={(check/"logs").as_posix()}',
            'server.memory.heap.initial_size=256m','server.memory.heap.max_size=768m','server.memory.pagecache.size=256m',
            f'server.jvm.additional=-Djdk.net.unixdomain.tmpdir={Path(state["short_temp_dir"]).as_posix()}',
            'dbms.usage_report.enabled=false','dbms.fleet_manager.enabled=false','server.fleet_discovery.enabled=false'])+'\n',encoding='utf-8')
        env['NEO4J_CONF'] = str(verify_conf)
        admin(['database','load','neo4j',f'--from-path={delivery}','--overwrite-destination=false'],'restore.log')
        admin(['database','check','neo4j',f'--from-path-data={data/"databases"}',f'--from-path-txn={txns}',
            '--check-graph=true','--check-indexes=true','--check-counts=true','--check-property-owners=true',
            '--threads=2','--max-off-heap-memory=256m',f'--report-path={check}'],'consistency.log')
    finally:
        manager.start()
    dump = delivery/'bergwitzsee-v2-events-20261108.dump'
    (delivery/'neo4j.dump').rename(dump)
    url = make_url(dotenv_values(runtime/'postgres.env')['SOLVE_DATABASE_URL'])
    assert url.database == DBNAME and url.host == '127.0.0.1' and url.port == 55439
    env = os.environ.copy()
    env['PGPASSWORD'] = url.password
    backup = delivery/'bergwitzsee-v2-events-20261108.backup'
    pg_bin = Path('C:/Program Files/PostgreSQL/18/bin')
    with (delivery/'postgres_backup.log').open('wb') as log:
        subprocess.run([str(pg_bin/'pg_dump.exe'),'-h',url.host,'-p',str(url.port),'-U',url.username,'-d',url.database,
            '-Fc','--no-owner','--no-acl','-f',str(backup)],env=env,stdout=log,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,timeout=180,check=True)
    with (delivery/'postgres_archive_contents.txt').open('wb') as log:
        subprocess.run([str(pg_bin/'pg_restore.exe'),'--list',str(backup)],stdout=log,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,timeout=60,check=True)
    engine = create_engine(url)
    with engine.connect() as c:
        invalid = c.execute(text("SELECT count(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated")).scalar_one()
        assert invalid == 0
        foreign_keys = c.execute(text("SELECT count(*) FROM pg_constraint WHERE contype='f' AND connamespace IN (SELECT oid FROM pg_namespace WHERE nspname IN ('water_kg_2','water_events','water_kg_extension'))")).scalar_one()
    engine.dispose()
    result = dict(status='passed', neo4j_restored_store_consistency_passed=True, commands=commands,
                  postgres_backup_archive_listing_passed=True, postgres_backup_full_restore_test_performed=False,
                  validated_foreign_keys=foreign_keys, files=[])
    for p in [dump, backup]:
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        p.with_suffix(p.suffix+'.sha256').write_text(digest+'  '+p.name+'\n',encoding='ascii')
        result['files'].append(dict(file=p.name,bytes=p.stat().st_size,sha256=digest))
    save(delivery/'EXPORT_VALIDATION.json', result)
    print(json.dumps(result['files']),flush=True)

def document():
    from dotenv import dotenv_values
    from sqlalchemy import create_engine, text
    from neo4j import GraphDatabase
    neo = json.loads((OUT/'NEO4J_VALIDATION.json').read_text(encoding='utf-8'))
    exported = json.loads((OUT/'export/EXPORT_VALIDATION.json').read_text(encoding='utf-8'))
    assert neo['status'] == exported['status'] == 'passed'
    cfg = dotenv_values(OUT/'runtime/neo4j_preview/preview.env')
    with GraphDatabase.driver(cfg['NEO4J_URI'],auth=(cfg['NEO4J_USER'],cfg['NEO4J_PASSWORD'])) as d:
        with d.session() as s:
            assert s.run('MATCH(n) RETURN count(n)').single()[0] == neo['nodes']
            assert s.run('MATCH()-[r]->() RETURN count(r)').single()[0] == neo['relationships']
            assert s.run('MATCH(:WaterBodyV2 {water_body_id:"DELW_DEST_EL03OW07-00"})-[:HAS_EVENT]->(e:Event) RETURN count(e)').single()[0] == 73
    engine = create_engine(dotenv_values(OUT/'runtime/postgres.env')['SOLVE_DATABASE_URL'])
    with engine.connect() as c:
        topics = [dict(r) for r in c.execute(text('SELECT source_topic,count(*) AS records,min(measured_at) AS first_date,max(measured_at) AS last_date FROM water_kg_2.measurement GROUP BY source_topic ORDER BY source_topic')).mappings()]
    engine.dispose()
    ext = json.loads((OUT/'BERGWITZSEE_EXTENSION.json').read_text(encoding='utf-8'))
    save(OUT/'DELIVERY_STATUS.json',dict(status='passed',date='2026-11-08',database=DBNAME,
        nodes=neo['nodes'],relationships=neo['relationships'],measurements=23803,events=73,
        event_links=73,source_documents=15,evidence_entries=79,measurement_topics=topics,
        fresh_downloads=40,reused_lhw_archives=6,lhw_original_retrieval='2026-11-01',
        event_assignment_statuses=ext['audit']['assignment_statuses'],
        event_review_statuses=ext['audit']['review_statuses'],
        browser='http://127.0.0.1:7479/browser/',bolt='bolt://127.0.0.1:7693',desktop_modified=False))
    table = '\n'.join(f"| {r['source_topic']} | {r['records']} | {r['first_date']} – {r['last_date']} |" for r in topics)
    content = f'''# Bergwitzsee – PostgreSQL und Knowledgegraph

Erstellt und geprüft am 8. November 2026. Gewässerkennung `DELW_DEST_EL03OW07-00`.

## Ergebnis

Der neue Datenstand enthält {neo['nodes']:,} Knoten und {neo['relationships']:,} Beziehungen.
Das ursprüngliche Brandenburg-V2-Schema wird wiederverwendet. Die Originalimportskripte liegen unverändert unter `source_scripts`.
Die Daten stammen vom Bergwitzsee. Es wurden keine Brandenburg-Dummy-Messungen übernommen.

- 23.803 Messwerteinträge, 26 Messstellen, 329 Probenahmeabschnitte, 1.012 Parameter und 18 Einheiten.
- 56 Gewässerbewertungen. Messwerte, Jahresbewertungen und WRRL-Zustände bleiben getrennt.
- 73 recherchierte Extraktionseinträge unter dem Label `Event`, 79 Quellenbelege und 15 Quelldokumente.
- Drei getrennte Gewässer-Berichtsversionen für 2010, 2016 und 2022, außerdem drei Grundwasserkörper-Versionen.
- Zwei Schutzgebiete und ein räumlich überlappender Grundwasserkörper. Räumliche Überlappung belegt keinen kausalen Wasseraustausch.

Jedes Event ist direkt über `(WaterBodyV2)-[:HAS_EVENT]->(Event)` angebunden.
PostgreSQL verwendet dafür `water_events.event.water_body_id` als Fremdschlüssel auf `water_kg_2.water_body`.
Der Graphimport liest Events, Belege, Quelldokumente und Gewässerversionen aus PostgreSQL zurück und prüft sie gegen die recherchierten Originaleinträge.

## Herkunft und Abrufstände

40 Quellenabrufe wurden am 8. November neu durchgeführt, einschließlich WFS/EEA-Metadaten, Geodaten, Badegewässerdaten und der amtlichen Berichte.
Die sechs vollständigen LHW-Originalarchive vom 1. November 2026 wurden per SHA256 geprüft und unter `data/lhw/raw` erneut bereitgestellt.
Diese sechs Dateien wurden heute nicht erneut aus dem Portal exportiert. Ihre sitzungsgebundenen Downloadadressen werden nicht als permanente API dargestellt.
Die historischen WRRL-Oberflächengewässerdaten und Events stammen aus dem bereits geprüften Recherchebestand.
Es gab in diesem Lauf keine neue Event-Recherche und keinen Nachweis, dass das GLD seit dem 1. November keine neuen Messungen veröffentlicht hat.

- [LHW-Datenportal](https://gld.lhw-sachsen-anhalt.de/)
- [Badegewässerdaten Bergwitzsee](https://ms.sachsen-anhalt.de/themen/gesundheit-und-pflege/daten-zu-gesundheit/badegewaesser/page/bergwitzsee)
- Exakte weitere URLs, Abrufzeiten, Dateihashes und Geometrieprüfungen stehen unter `data/geodata` und `data/monitoring`.

| Messbereich | Einträge | Beobachtungszeitraum |
|---|---:|---|
{table}

Die Zahl der Messwerteinträge zählt einzelne Werte, nicht unabhängige Proben. Eine biologische Quellzeile kann mehrere Kennzahlen enthalten.
2.634 Werte mit Vergleichszeichen werden nicht als exakte Konzentrationen ausgegeben. Originaltext, Grenzwertangabe und Einheit bleiben erhalten.
Bei den Badegewässerdaten ist die Art der berichteten analytischen Grenze nicht genauer bestimmt.

## Events und Unsicherheiten

`Event` ist hier der Klassenname für Rechercheeinträge. `record_type` unterscheidet Ereignisse, Messungen, Bewertungen, Zustände und Planungen.
Die fachliche Prüfung bleibt offen. 71 Einträge sind als fachlich prüfbedürftig und zwei als Human Review offen gekennzeichnet.
Bei `BW-WASSERHALTUNG-1955` und `BW-GRUNDWASSER-1959` ist die Zuordnung zum Gewässer nur wahrscheinlich. Dieser Status bleibt an den Einträgen und Verbindungen erhalten.
Quellenbelege und Webadressen sind über `HAS_EVIDENCE` und `FROM_DOCUMENT` abrufbar.

## Öffnen

Neo4j Browser läuft unter http://127.0.0.1:7479/browser/ mit Verbindung `bolt://127.0.0.1:7693`, Benutzer `neo4j`, Datenbank `neo4j`.
Das lokale Kennwort steht in `runtime/neo4j_preview/preview.env`. Keine Zugangsdaten wurden in die Sicherungsdateien oder diese Anleitung geschrieben.
Die bestehende Neo4j-Desktop-Instanz wurde nicht geändert. Der neue Graph ist eine eigene lokale Instanz.

```cypher
MATCH p=(w:WaterBodyV2 {{water_body_id: 'DELW_DEST_EL03OW07-00'}})-[:HAS_EVENT]->(e:Event)
RETURN p;
```

PostgreSQL läuft auf `127.0.0.1:55439`, Datenbank `{DBNAME}`. Die private Verbindungsdatei ist `runtime/postgres.env`.
Schemas sind `water_kg_2`, `water_raw`, `water_events` und `water_kg_extension`.

```sql
SELECT w.name, e.event_id, e.payload->>'statement' AS statement
FROM water_events.event e
JOIN water_kg_2.water_body w USING (water_body_id);
```

## Sicherungen und Prüfungen

- `export/bergwitzsee-v2-events-20261108.dump` für Neo4j Desktop, erzeugt mit Neo4j 2026.10.1.
- `export/bergwitzsee-v2-events-20261108.backup` für PostgreSQL 18 mit PostGIS.
- Neben beiden Dateien stehen SHA256-Prüfsummen. Der Neo4j-Dump wurde separat geladen und nativ auf Graph-, Index-, Zählungs- und Eigenschaftskonsistenz geprüft.
- Die PostgreSQL-Sicherung wurde erfolgreich erstellt und ihr Inhaltsverzeichnis geprüft. Ein kompletter Wiederherstellungstest dieser PostgreSQL-Sicherung wurde nicht ausgeführt.
- Alle ursprünglichen Graphattribute wurden nach dem Import gegen PostgreSQL geprüft. Die Event-Quellinhalte und alle direkten Eventverbindungen wurden abgeglichen.

Die vollständigen Prüfprotokolle sind `POSTGRES_VALIDATION.json`, `NEO4J_VALIDATION.json`, `data/geodata/validation.json` und `export/EXPORT_VALIDATION.json`.

## Noch fehlende Daten

Nicht belegt sind eigenständige stationsbezogene WRRL-Bewertungen, vollständige Fischmessreihen, Einzugsgebiets- und Planungseinheit-Polygone, substanzweise WRRL-Chemiezustände sowie Sedimentkern-/Paläoreihen.
Die zugehörigen ursprünglichen Tabellen bleiben erhalten und leer. Sedimentchemie allein wird nicht zu einer Paläoreihe umgedeutet.
Fehlende Einheiten, Laborverfahren und uneindeutige Messstellenkoordinaten werden nicht erfunden.

## Skripte

Der Ablauf liegt in `scripts/bergwitzsee_enrichment/refresh_20261108.py` im SOLVE-Projekt. Die Phasen heißen `acquire`, `postgres`, `setup_graph`, `graph`, `export`, `document`.
Die abgeschlossenen Import- und Exportphasen lassen sich nicht versehentlich über bestehende Daten schreiben. Für einen weiteren vollständigen Datenstand sind neue Zielpfade und Datenbanknamen erforderlich.
Die lokale Neo4j-Instanz lässt sich mit `runtime/manage_neo4j_preview.py start`, `status` bzw. `stop` verwalten. Dafür den Projekt-Python unter `outputs/wasserblick33_20261031/runtime/venv/Scripts/python.exe` benutzen.
'''
    (OUT/'README.md').write_text(content, encoding='utf-8')
    print('Delivery documented and running databases verified.',flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['acquire', 'postgres', 'setup_graph', 'graph', 'export', 'document'])
    args = parser.parse_args()
    globals()[args.action]()

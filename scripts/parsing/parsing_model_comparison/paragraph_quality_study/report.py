from pathlib import Path
import sys, statistics
from collections import Counter
ROOT=Path(__file__).resolve().parent
CODE=ROOT.parent.parent/'SOLVE_Parsing_Fein_Batch_1.2.0'
sys.path.insert(0,str(CODE))
from common import read_json,read_jsonl,write_json,atomic_text,file_hash
from prepare_document import normalize_quote
plan=read_json(ROOT/'plan.json')
previous_plan=read_json(ROOT.parent/'chunk_size_study/plan.json')
assert plan['quality_checklist']==previous_plan['quality_checklist']
assert normalize_quote('\n'.join(b['text'] for b in plan['blocks']))==normalize_quote('\n'.join(b['text'] for b in previous_plan['blocks']))
manual=read_json(ROOT/'manual_review.json')
paths=sorted((ROOT/'results').glob('*/result.json'))
assert len(paths)==2
runs=[]
for path in paths:
    r=read_json(path);assert r['all_complete']
    config=read_json(path.parent/'manifest.json')['config']
    assert all(file_hash(CODE/name)==sha for name,sha in config['code_hashes'].items())
    assert [b for c in config['chunks'] for b in c['blocks']]==plan['blocks']
    ratings=manual['runs'][r['name']]['ratings'];assert len(ratings)==19
    logs=read_jsonl(path.parent/'logs/api_requests.jsonl')
    r['checklist']=dict(Counter(ratings))
    r['http_statuses']=dict(Counter(str(x.get('http_status')) for x in logs if x['event']=='request_finished'))
    r['repair_statuses']=dict(Counter(a['kind']+':'+a['status'] for p in r['packages'] for a in p.get('relationship_review',[])))
    r['repair_reasons']=[a.get('reason') for p in r['packages'] for a in p.get('relationship_review',[]) if a.get('reason')]
    r['self_relationships_exported']=sum(x['source_event_id']==x['target_event_id'] for x in read_jsonl(path.parent/'fein/relationships.jsonl'))
    r['finish_reasons']=dict(Counter(read_json(p).get('choices',[{}])[0].get('finish_reason') for p in (path.parent/'fein/raw_responses').glob('*.json')))
    runs.append({k:v for k,v in r.items() if k!='packages'})
old=[r for r in read_json(ROOT.parent/'chunk_size_study/verified_metrics.json') if r['size']==4000]
summary={'normalized_source_identical':True,'old_blocks':18,'new_blocks':12,
         'target_chars':4000,'actual_chunk_chars':runs[0]['primary_chars'],
         'runs':runs,'baseline_runs':old,'offline_tests_passed':59,
         'limitations':'Two repetitions of one document, same source and model settings; multiple parser changes together, nonconcurrent baseline, no general optimum or full semantic correctness demonstrated.'}
write_json(ROOT/'verified_summary.json',summary)
def duration(s):
    s=round(s);return f'{s//60}:{s%60:02d}'
lines=['# Parser 1.2.0: Absätze erhalten und Vergleichsangaben verbessern','',
       'Stand: 29.09.2026. Umsetzung und Vergleich mit Version 1.1.0 anhand derselben PDF-Seiten 10, 11 und 17.','',
       '## Was geändert wurde','',
       '- 4.000 Zeichen als weiche Zielgröße: Den Absatz bzw. die Tabellenzeile vollständig übernehmen, der/die die Zielgröße erreicht oder überschreitet.',
       '- Keine künstliche Zerlegung nach 1.600 Zeichen oder halber Abschnittsgröße bei der Aufbereitung. Im Test: 12 statt 18 Blöcke; normalisierter Quelltext identisch.',
       '- Tatsächliche Abschnittsgrößen im Test: 4.306 und 3.446 Zeichen. Auch die technische Wiederherstellung teilt keinen einzelnen Absatzblock; bei unüberwindbaren Grenzen wird ein expliziter Fehler ausgewiesen.',
       '- Optionale Zeitangaben je Wert: values[].time_original und values[].time_normalized. Hauptspalten der Excel-Exporte bleiben gleich.',
       '- Zulässige Themen-/Klassen-/Beziehungstyp-IDs im API-Schema; lokale Validierung bleibt erhalten.',
       '- Klarere Belegregeln und eine begrenzte Qualitätsreparatur für ungültige Belege/IDs sowie erkennbare unvollständige Zahlenvergleiche. Vorhandene Fachinhalte werden geschützt; kein vollständiger semantischer Vollständigkeitsnachweis.',
       '', '## Messungen','',
       '| Version | Lauf | Zeit (min:s) | API-Aufrufe | Exportierte Fakten / Beziehungen | Vollständige Prüfpunkte / 19 | Teilweise |',
       '|---|---:|---:|---:|---:|---:|---:|']
for version,group in [('1.1.0, bisherige 4.000-Zeichen-Aufteilung',old),('1.2.0, ganze Absätze',runs)]:
    for r in sorted(group,key=lambda x:x['repeat']):
        q=r['checklist']
        lines.append(f'| {version} | {r["repeat"]} | {duration(r["elapsed_seconds"])} | {r["http_attempts"]} | {r["exported_events"]} / {r["exported_relationships"]} | {q.get("F",0)} | {q.get("P",0)} |')
lines+=['',f'Mittlere Laufzeit bisher: **{duration(statistics.mean(r["elapsed_seconds"] for r in old))}**, neue Version: **{duration(statistics.mean(r["elapsed_seconds"] for r in runs))}**. Dies sind Beobachtungen dieses kleinen Piloten, keine belastbare allgemeine Beschleunigungsrate.',
        '', 'Alle Zeiten umfassen Extraktion, zusätzliche Reparaturen, Validierung und Export. Gleiche Qwen-Modellkennung, Temperatur 0, Thinking aus, 16.000 Ausgabetokens, ein Worker, 30 RPM. Keine Wiederverwendung alter Antworten. PDF-Aufbereitung und Modellkatalogabfrage sind ausgeschlossen.',
        '', '## Qualität und Grenzen','',
        '**Ergebnis:** Die geprüfte Gesamtvollständigkeit ist im kleinen Pilot unverändert: sechs bis acht von 19 Prüfpunkten vollständig, im Mittel sieben. Die Zuordnung der Moldau-Vergleichswerte zu ihren Zeiträumen verbessert sich konkret; der Eger-Vergleich gelingt im ersten Lauf, scheitert im zweiten erneut an Belegen. Die durchschnittlich kürzere beobachtete Laufzeit ist deshalb kein Beweis für einen allgemeinen Qualitätsgewinn.',
        '',
        'Die 19 Prüfpunkte wurden aus dem vorigen Test unverändert übernommen. F = vollständig, P = teilweise, M = fehlt, E = wesentliche unbelegte Ergänzung. Angaben nur im Beleg zählen nicht als extrahiert; Wiederholungen erhöhen die Abdeckung nicht. Nicht verblindete Prüfung durch den Assistenten, kein unabhängiges Expertenrating.',
        '', 'Zulässige IDs und wörtliche Belege garantieren keine richtige Ontologieklasse oder kausale Interpretation. Es bleiben Auslassungen und teilweise Dubletten. Die kleine Vergleichsstichprobe umfasst ein Dokument; geänderte Aufbereitung, Prompt, Schema und Nachprüfung wirken gemeinsam. Die alten Läufe fanden früher statt, Serverlast kann die Laufzeiten beeinflussen. Ein allgemeiner Qualitäts- oder Geschwindigkeitsgewinn lässt sich daraus nicht ableiten.',
        '', '### Einzelbewertungen','']
for r in runs:
    entry=manual['runs'][r['name']]
    lines += ['#### '+r['name'],'','Prüfpunkte 1–19: '+', '.join(entry['ratings']),'']
    lines.extend('- '+k+': '+v for k,v in entry['notes'].items())
    lines += ['', 'Reparaturstatus: '+str(r['repair_statuses']), 'Ablehnungsgründe: '+str(r['repair_reasons']),'']
lines+=['## Prüfung und Nachvollziehbarkeit','',
        '- 59 Offline-Tests erfolgreich: weiche Grenze, lange Absätze, Tabellenzeilen, ID-Enums, wertbezogene Zeiträume, Mehrblock-Belege, Reparaturgrenzen und bisherige Batch-/Selbstbeziehungsregressionen.',
        '- Ein Fehler der ersten Vergleichserkennung wurde im Prototyp erkannt: Ein früheres Speichervolumen durfte nicht mit einem späteren Abflusswert verbunden werden. Die korrigierte Erkennung überschreitet weder einen neuen numerischen von-Ausdruck noch Satzgrenzen. Zwei Regressionstests sichern dies ab.',
        '- Der unterbrochene Prototyp unter paragraph_quality_prototype ist nicht Teil der obigen Messungen. Alle gewerteten Abschlussläufe entstanden nach der Korrektur mit frischen Antworten.',
        '- plan.json, source_equivalence.json, manifests, Rohantworten und Exporte liegen unter parsing_model_comparison/paragraph_quality_study. verified_summary.json und manual_review.json enthalten Messwerte und Einzelbewertungen.',
        '- Beim Erstellen des Berichts wurden identischer normalisierter Quelltext, vollständige Übergabe aller Blöcke und unveränderter getesteter Python-Code geprüft.',
        '- Alte Versionen und Ergebnisordner bleiben erhalten. Für Version 1.2.0 einen neuen Ergebnisordner verwenden.']
atomic_text(ROOT/'Testbericht.md','\n'.join(lines)+'\n')
print('Verified two runs; report generated.')

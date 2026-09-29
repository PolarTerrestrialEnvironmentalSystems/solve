"""Render auditable benchmark report; never infer quality from output count."""
from pathlib import Path
import json,sys,statistics
from collections import Counter
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent.parent/'SOLVE_Parsing_Fein_Batch_1.1.0'))
from common import read_json,read_jsonl,atomic_text,write_json,file_hash
plan=read_json(ROOT/'plan.json')
review=read_json(ROOT/'manual_review.json')
runs=[read_json(p) for p in sorted((ROOT/'results').glob('*/result.json'))]
assert len(runs)==6, 'Need all six trials'
assert all(r['all_complete'] for r in runs)
assert all(len(review['runs'][r['name']]['ratings'])==19 for r in runs)
expected=plan['blocks']
for r in runs:
    manifest=read_json(ROOT/'results'/r['name']/'manifest.json')['config']
    assert [b for c in manifest['chunks'] for b in c['blocks']]==expected
    assert all(file_hash(ROOT.parent.parent/'SOLVE_Parsing_Fein_Batch_1.1.0'/name)==sha for name,sha in manifest['code_hashes'].items())
    assert manifest['settings']==read_json(ROOT/'results'/runs[0]['name']/'manifest.json')['config']['settings']
    r['checklist']=dict(Counter(review['runs'][r['name']]['ratings']))
    log=read_jsonl(ROOT/'results'/r['name']/'logs/api_requests.jsonl')
    r['http_statuses']=dict(Counter(str(x.get('http_status')) for x in log if x['event']=='request_finished'))
    r['self_relationships_exported']=sum(x['source_event_id']==x['target_event_id'] for x in read_jsonl(ROOT/'results'/r['name']/'fein/relationships.jsonl'))
    r['finish_reasons']=dict(Counter(read_json(p).get('choices',[{}])[0].get('finish_reason') for p in (ROOT/'results'/r['name']/'fein/raw_responses').glob('*.json')))
write_json(ROOT/'verified_metrics.json',[{k:v for k,v in r.items() if k!='packages'} for r in runs])
def duration(s):
    s=round(s);return f'{s//60}:{s%60:02d}'
lines=['# Abschnittsgrößen: kontrollierter Pilot vom 29.09.2026','',
       'Getestet wurde der unveränderte Parser 1.1.0 mit Qwen3.8-Flash-Next-NVFP4 über Blablador. **Größere Abschnitte waren in diesem Pilot schneller; Geschwindigkeit allein ist aber kein Qualitätsgewinn.** Die Tabelle trennt Laufzeit, automatisch akzeptierte Ausgaben und manuell geprüfte Vollständigkeit.','',
       '## Versuchsaufbau','',
       f'- Identischer Quelltext: {plan["source_chars"]} Zeichen aus PDF-Seiten 10, 11 und 17 des bereitgestellten Elbe-Berichts. PDF-SHA256: `{plan["source_sha256"]}`.',
       '- Grenzen: 2.000 / 4.000 / 10.000 Zeichen, entsprechend fünf / zwei / einem Anfangsabschnitt. Der große Abschnitt enthält tatsächlich 7.746 Zeichen.',
       '- Je zwei frische Durchgänge: zuerst klein → mittel → groß, danach groß → mittel → klein. Kein Wiederverwenden gespeicherter Modellantworten in den gemessenen Läufen.',
       '- Identische feste Quellblöcke, Prompt, Schema, Ontologie und Einstellungen: Temperatur 0, Thinking aus, 16.000 Ausgabetokens, 65.536 Kontexttokens, ein Worker, 30 RPM, maximal ein HTTP-Retry. Selbstbeziehungsschutz aktiv.',
       '- Die native Übergabe angrenzender Quellblöcke bleibt aktiv. Zusätzlich übermittelte Kontextzeichen pro vollständigem Lauf: 5.370 / 426 / 0. Das gehört zum Verhalten der jeweiligen Abschnittsgröße.',
       '- Gemessen: Extraktion einschließlich Reparaturen, Wiederholungen, möglicher Teilungen, bis zu zwei ergänzender Kontextanfragen, Validierung und Export. PDF-Aufbereitung und Modellkatalogabfrage sind ausgeschlossen.',
       '- Die Vorverarbeitung wurde einmalig übernommen, damit Unterschiede bei der PDF-Aufbereitung nicht den Größenvergleich verfälschen. Dies ist deshalb kein kompletter Vergleich aller Auswirkungen des CLI-Parameters auf neue PDF-Aufbereitungen.',
       '- Ein erster unterbrochener Versuch mit mehrfachen HTTP-502-Fehlern liegt separat unter `interrupted_r1_chars2000_502`; er wurde nicht gewertet. Eine kurze Erreichbarkeitsprobe ist ebenfalls ausgeschlossen.','',
       '## Einzelmessungen','',
       '| Grenze | Durchgang | Zeit (min:s) | API-Aufrufe | Fakten exportiert | Beziehungen exportiert | Prüfpunkte vollständig / 19 | Teilweise | Fehlerhafte Ergänzung |',
       '|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
for r in sorted(runs,key=lambda x:(x['size'],x['repeat'])):
    q=r['checklist']
    lines.append(f'| {r["size"]} | {r["repeat"]} | {duration(r["elapsed_seconds"])} | {r["http_attempts"]} | {r["exported_events"]} | {r["exported_relationships"]} | {q.get("F",0)} | {q.get("P",0)} | {q.get("E",0)} |')
lines+=['','## Mittelwerte','', '| Grenze | Mittlere Zeit | Vollständige Prüfpunkte, Mittel / 19 |', '|---:|---:|---:|']
for size in plan['sizes']:
    group=[r for r in runs if r['size']==size]
    lines.append(f'| {size} | {duration(statistics.mean(r["elapsed_seconds"] for r in group))} | {statistics.mean(r["checklist"].get("F",0) for r in group):.1f} |')
lines+=['','## Einordnung und Empfehlung','',
        '**Nicht pauschal vergrößern.** Der große Abschnitt ist schnell, verliert hier aber erhebliche Inhalte. In einem seiner zwei Läufe wurden alle Einträge wegen ungültiger Themen-IDs ausgesondert. Auch seine Rohantworten enthalten nur acht bzw. zehn Events; Filterfehler erklären die fehlende Abdeckung daher nicht allein.',
        '', 'Die kleinen Abschnitte decken jeweils neun der 19 Prüfpunkte vollständig und vier teilweise ab, kosten aber deutlich mehr Zeit. Ihre 42 bzw. 44 exportierten Fakten enthalten mehrere inhaltliche Dubletten. Unbelegte Zeitzuordnungen (z. B. Beginn einer Betriebsregel 1964) bestehen fort. Klein ist deshalb ebenfalls keine Garantie für korrekte Ergebnisse.',
        '', '4.000 Zeichen sind hier ein sinnvoller Kandidat für weitere Optimierung: rund 51 % weniger Laufzeit als 2.000 Zeichen, allerdings nur sechs bzw. acht vollständig erhaltene Prüfpunkte. Das ist ein beobachteter Kompromiss, kein nachgewiesenes allgemeines Optimum. Vor einer dauerhaften Umstellung sollten zulässige Themen-IDs, Beleggrenzen und Vollständigkeit verbessert und derselbe Vergleich mit weiteren Dokumenten wiederholt werden.',
        '', 'In den sechs gewerteten Läufen waren alle 23 API-Aufrufe HTTP 200 (16 Extraktionen, sieben Selbstbeziehungsreparaturen). Alle Antworten endeten regulär mit `stop`; es gab keine automatischen Teilungen und keine zusätzlichen Kontextanfragen. Kein finaler Export enthält eine Beziehung mit identischer Quell- und Ziel-ID. Das schließt andere semantische Beziehungsfehler nicht aus.']
lines+=['','## Qualitätsmaß und Grenzen','',
        'Die 19 Prüfpunkte wurden vor den Modellantworten festgelegt und stehen in `plan.json`. Geprüft werden belegte Werte, zugehörige Zeiträume, Sachverhalte und Prognosestatus in den finalen Fachfeldern. Ein Fakt, der nur im langen Belegzitat vorkommt, zählt nicht als extrahiert. F = vollständig, P = teilweise, M = fehlt, E = vorhanden, aber mit einer wesentlichen unbelegten Ergänzung. Wiederholungen desselben Fakts erhöhen die Abdeckung nicht.',
        '', 'Dies ist eine begrenzte, nicht verblindete Prüfung durch den Assistenten, kein unabhängiges Expertenrating und keine allgemeine Precision-/Recall-Messung. Die Abdeckung misst Sachverhalte; sie ersetzt keine gesonderte Prüfung korrekter Ontologieklassen oder eines vollständigen Beziehungsgraphen. Auch automatisch exportierte Zeilen können semantische Fehler enthalten.',
        '', 'Ein Dokument, drei ausgewählte Seiten und zwei Wiederholungen erlauben keine statistisch belastbare allgemeine optimale Abschnittsgröße. Tabellen, Bilder und größere Abschnitte oberhalb der gesamten 7.746 Zeichen wurden damit nicht bewertet. Serverlast kann die Zeiten beeinflussen.',
        '', '## Dokumentierte Einzelbefunde','']
criteria=[
    'Moldau QM364: 20,0 → 38,8 m³/s samt beiden Zeitreihen und Talsperrenwirkung',
    'Moldau QM364: 36,9 m³/s für 1961–2005',
    'Berounka-Anteil 3,8 m³/s innerhalb der 38,8 m³/s',
    'Moldaukaskade: neun Talsperren, 1.352,58 Mio. m³ Stauraum',
    'Vrané: Mindestabgabe 40,0 m³/s',
    'Prag vor Talsperrenbau: 12,0–15,0 m³/s',
    'Nechranice: Stauraum 272,43 Mio. m³; Wirkung ab 1966, offizielle Inbetriebnahme 1968',
    'Eger/Louny QM364: 2,7 → 8,0 m³/s mit beiden Zeitreihen und Talsperrenwirkung',
    'Jesenice: 52,75 Mio. m³, Inbetriebnahme 1961',
    'Skalka: 15,92 Mio. m³, Inbetriebnahme 1964',
    'Nechranice: Mindestabgabe 8,0 m³/s',
    'Niedrigwasseraufhöhung der Eger wirkt auch auf die Elbe unterhalb der Mündung',
    'Prognose tschechisches Elbegebiet: Jahresabfluss −10 %, beide Vergleichszeiträume',
    'Prognose Moldau: mittlerer Abfluss −10 bis −15 %',
    'Prognose: mehr winterlicher Regen/Niederschlag → mehr Abfluss/Hochwasser',
    'Prognose: weniger Sommerregen → weniger ober-/unterirdischer Abfluss → schärferes Niedrigwasser',
    'Grenz-Einzugsgebiet 51.394 km²; Talsperrenanteil 21.372 km² / 41,6 %',
    'Künftiger Konflikt: Hochwasserrückhalteraum versus Speicherung zur Niedrigwasserabgabe',
    'Prognose häufigerer Unterschreitungen vereinbarter Monatsminima, besonders im Sommer'
]
ordered=sorted(runs,key=lambda r:(r['size'],r['repeat']))
lines+=['### Alle 19 Prüfpunkte','', '| Prüfpunkt | 2k/1 | 2k/2 | 4k/1 | 4k/2 | 10k/1 | 10k/2 |', '|---|---|---|---|---|---|---|']
for i,label in enumerate(criteria):
    lines.append('| '+str(i+1)+'. '+label+' | '+' | '.join(review['runs'][r['name']]['ratings'][i] for r in ordered)+' |')
lines+=['', 'F = vollständig, P = teilweise, M = fehlt, E = wesentliche unbelegte Ergänzung. Prüfpunkte beziehen sich auf finale Fachfelder einschließlich Aussagen; nur im Zitat enthaltene Informationen zählen nicht.','']
for name,entry in sorted(review['runs'].items()):
    lines.append('### '+name)
    lines.append('')
    for key,note in entry['notes'].items(): lines.append(f'- {key}: {note}')
    lines.append('')
lines+=['## Nachvollziehbarkeit','',
        '- `run_study.py`: reproduzierbarer Versuchsablauf, Abbruch bei unvollständiger Extraktion; vorhandene Messungen werden niemals mit Cache-Zeiten überschrieben.',
        '- `results/*/manifest.json`: Eingaben, Einstellungen, Modellkennung und Prüfsummen.',
        '- `results/*/fein/`: Rohantworten, Checkpoints, JSONL-/Excel-Exporte und Review-Gründe.',
        '- `manual_review.json`: alle 19 Bewertungen je Lauf mit Erläuterungen.',
        '- `verified_metrics.json`: gemessene Zeiten, Tokenzahlen, Fehlergründe, HTTP-Status und Checklistenwerte.',
        '- Quelltextgleichheit aller sechs Varianten, gleiche Einstellungen und unveränderter Parsercode wurden beim Erstellen dieses Berichts geprüft.',
        '- Produktionscode und Standard-Abschnittsgröße wurden nicht geändert.']
atomic_text(ROOT/'Testbericht.md','\n'.join(lines)+'\n')
print('Verified six trials; report written.')

"""Generate the human-readable pilot report from recorded measurements."""
import collections
import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent
out = root / 'validation/pilot_5_comparison'
data = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
events = json.loads((out / 'progress.json').read_text(encoding='utf-8'))
snapshots = [json.loads(p.read_text(encoding='utf-8')) for p in (out / 'snapshots').glob('search_*.json')]
fetches = [json.loads(p.read_text(encoding='utf-8')) for p in (out / 'snapshots').glob('fetch_*.json')]
totals = {}
for strategy in ['baseline', 'staged_greedy', 'staged_epsilon']:
    rows = [r for r in data['results'] if r['strategy'] == strategy]
    totals[strategy] = {k: sum(r[k] for r in rows) for k in ['searches', 'source_attempts', 'reviewed', 'unique_archived', 'relevant_unique', 'partial_searches']}
    totals[strategy]['topic_pairs'] = sum(len(r['topics']) for r in rows)
    totals[strategy]['wall_seconds'] = round(sum(e.get('wall_seconds', 0) for e in events if e['strategy'] == strategy), 1)
    totals[strategy]['exploration_queries'] = sum('Erkundung' in e.get('reason', '') for e in events if e['strategy'] == strategy)

lines = ['# Pilot: drei Suchstrategien für fünf Gewässer', '',
    'Verglichen wurden Arendsee, Bergwitzsee, Barleber See I, Barleber See II und Rappbodetalsperre/Vorsperre Hassel.', '',
    '**Ergebnis: Kein belastbarer Sieger.** Alle drei Strategien fanden dieselben zwei automatisch relevanten Kandidaten zum Arendsee. Die manuelle Prüfung bestätigt davon einen als thematisch passenden Sekundärquellen-Kandidaten; die Stadtgeschichte-Zuordnung des anderen ist ein Fehlalarm. Für die anderen vier Gewässer wurde kein regel-relevantes Dokument gefunden. Alle 60 Suchschritte meldeten Ausfälle einzelner Suchmaschinen. Dies ist ein Ergebnis dieses begrenzten Piloten, kein Nachweis fehlender Quellen.', '',
    '## Fragestellung und Verfahren', '',
    '- Baseline: bisherige breite Suchplanung und bisherige Auswahlprioritäten.',
    '- Staged Greedy: Übersicht, PDF-Suche, anschließend Auswahl nach Quellengewinn und offenen Themen.',
    '- Staged Epsilon: dieselben Stufen mit reproduzierbarer Erkundung bei einer konfigurierten Wahrscheinlichkeit von 20 %.',
    '- Identische verbesserte Namensprüfung, Relevanzregeln und Linkpriorisierung für alle drei Varianten.',
    '- Vier Suchversuche je Gewässer und Strategie, bis zu drei Quellenprüfungen nach jedem Versuch.',
    '- Rotierende Ausführungsreihenfolge; erste Suchantworten und Quellabrufe werden eingefroren und gemeinsam wiederverwendet.',
    '- Getrennte Dossiers pro Gewässer und Strategie: keine Vermischung der Bewertungen; im Pilot kein Lernen zwischen Gewässern.',
    '- Bereits bekannte Dokumentbytes werden pro Gewässer nur einmal als Quellengewinn gewertet.', '',
    '## Gemessene Ergebnisse', '',
    '| Strategie | Suchversuche | Quellenversuche | Geprüfte Quellen | Archivierte SHA je Gewässer, summiert | Regel-relevante SHA je Gewässer, summiert | Themen-Gewässer-Paare | Suchen mit Engine-Fehlern |',
    '|---|---:|---:|---:|---:|---:|---:|---:|']
for strategy, t in totals.items():
    lines.append('| ' + strategy + ' | ' + ' | '.join(str(t[k]) for k in ['searches','source_attempts','reviewed','unique_archived','relevant_unique','topic_pairs','partial_searches']) + ' |')
lines += ['', 'Diese Relevanzzahlen sind automatische Kandidatenbewertungen, keine fachlich bestätigten Quellenzahlen. Summen zählen eine Quelle erneut, wenn sie einem weiteren Gewässer zugeordnet wurde.', '',
    '| Gewässer | Baseline: Kandidaten / Themen | Greedy: Kandidaten / Themen | Epsilon: Kandidaten / Themen |', '|---|---:|---:|---:|']
names = list(dict.fromkeys(r['name'] for r in data['results']))
for name in names:
    rows = {r['strategy']: r for r in data['results'] if r['name'] == name}
    lines.append('| ' + name + ' | ' + ' | '.join(f"{rows[k]['relevant_unique']} / {len(rows[k]['topics'])}" for k in totals) + ' |')
lines += ['', '## Suchdienst und Aussagegrenzen', '',
    f"Für den Vergleich wurden {len(snapshots)} unterschiedliche Suchantworten aufgezeichnet; {sum(bool(d.get('warnings')) for d in snapshots)} enthielten Suchmaschinenfehler. Zusätzlich wurden {len(fetches)} URL-Abrufresultate eingefroren.", '',
    'Beobachtet wurden insbesondere CAPTCHA-/Rate-Limit-Meldungen sowie Fehler einzelner Engines. Mehrere Anfragen zu Bergwitzsee, Barleber See und Vorsperre Hassel lieferten offensichtlich themenfremde Treffer. Eine zusätzliche Diagnose mit dem einzelnen Suchwort Bergwitzsee bestätigte das Verhalten; Ursache auf Server-/Engine-Seite wurde nicht nachgewiesen.', '',
    'Die Stichprobe erlaubt daher keine belastbare Aussage, dass eine Suchstrategie generell überlegen ist. Es gibt keinen vollständigen, manuell annotierten Referenzbestand; Recall und statistische Signifikanz werden nicht behauptet. Vier Anfragen je Gewässer sind ein kleiner Funktionstest der Suchstufen.', '',
    '## Laufzeit', '',
    f"Die einmaligen Suchaufrufe beanspruchten zusammen {sum(d['seconds'] for d in snapshots):.1f} Sekunden einschließlich Suchpausen; die eingefrorenen URL-Abrufe {sum(d['seconds'] for d in fetches):.1f} Sekunden einschließlich robots-Prüfungen, Wartezeiten und Fehlern.", '',
    '| Strategie | Gemessene Bearbeitungszeit im geteilten Cache (s) | Explizite Erkundungsanfragen |', '|---|---:|---:|']
for strategy, t in totals.items():
    lines.append(f"| {strategy} | {t['wall_seconds']} | {t['exploration_queries']} |")
lines += ['', '**Diese Zeiten sind kein Geschwindigkeitsranking:** Später ausgeführte Varianten profitieren von bereits abgerufenen Antworten. Parserlaufzeit, Suchzeit und Netzwerkcache sind vermischt.', '',
    '## Regeln und verbleibende Risiken', '',
    'Die frühere Teilzeichenfolgensuche konnte Barleber See II als See I erkennen. Das wurde durch Namensgrenzen behoben. Explizite Ausschlussnamen können Haupt- und Vorsperren trennen. Themen müssen nun im lokalen Namensumfeld auftreten. Das bleibt eine Heuristik und erkennt nicht zuverlässig, ob eine Aussage Stadtgeschichte oder Gewässergeschichte betrifft.', '',
    'Der adaptive Quellengewinn beträgt 3 Punkte für ein neues regel-relevantes Dokument plus 2 Punkte pro erstmals abgedecktem Thema. Technische Fehler und ergebnislose Teilausfälle werden nicht als negative Evidenz angerechnet. Nach vier informativen Suchen ohne Gewinn kann eine Route pausieren. UCB/Thompson Sampling wurden nicht implementiert.', '',
    '## Nachvollziehbarkeit', '',
    '- `summary.json`: Ergebniskennzahlen und vollständiges Feedback pro Suchauftrag.',
    '- `events.jsonl` und `progress.json`: tatsächlich ausgewählte Anfragen, Gründe und Versuchszahlen.',
    '- `snapshots/`: Suchtreffer-Metadaten, Engine-Fehler, eingefrorene Abrufresultate.',
    '- `shared_sources/archive/`: Originaldateien, keine extrahierten Volltexte.',
    '- `dossiers/`: getrennte SQLite-Zustände und Exportdateien.',
    '- `../adaptive_tests.txt`: automatisierte Regressionstests.', '',
    'Die aktive Auswahl von 33 Gewässern bleibt erhalten. Die neuen Strategien werden über separate Pilotkonfigurationen gestartet; die Baseline bleibt bis zu einem belastbaren Vergleich Standard.', '']
audit = out / 'manual_audit.json'
if audit.exists():
    lines += ['## Manuelle Stichprobe', '']
    for item in json.loads(audit.read_text(encoding='utf-8')):
        lines += [f"- {item['url']}: {item['finding']}"]
    lines += ['', 'Diese Stichprobe ist keine vollständige fachliche Annotation sämtlicher Quellen.']
verification = out / 'verification.json'
if verification.exists():
    v = json.loads(verification.read_text(encoding='utf-8'))
    lines += ['', '## Verifikation', '',
        f"{v['tests_passed']} automatisierte Tests bestanden. Netzwerkfreier Replay: {v['replay_search_steps']} Schritte. Identische Anfrageentscheidungen: {v['decisions_identical']}; identische Quellenkennzahlen: {v['source_outcomes_identical']}. Details: `verification.json`."]
(out / 'VERGLEICH.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
(out / 'totals.json').write_text(json.dumps(totals, indent=2) + '\n', encoding='utf-8')
print(json.dumps(totals, indent=2))

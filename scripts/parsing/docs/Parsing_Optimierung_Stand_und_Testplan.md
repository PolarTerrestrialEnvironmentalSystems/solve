---
title: "SOLVE: Parsing-Optimierung – Befunde, Entscheidungen und Testplan"
date: "2026-09-28"
language: "de"
project: "SOLVE"
software_baseline: "SOLVE_Parsing_Fein_Batch_1.0.0"
optimization_status: "proposed_not_implemented_or_benchmarked"
target_ontology: "fein"
api_provider: "Blablador"
configured_rate_limit_rpm: 30
existing_default_workers: 4
---

# Stand und Ziel

Die bestehende Pipeline extrahiert Sachverhalte aus PDFs und ordnet sie Event- und Beziehungstabellen zu. Eine teilbare Softwareversion verarbeitet bereits mehrere PDFs aus einem Eingabeordner ausschließlich mit der feinen Ontologie. Sie unterstützt Checkpoints, Wiederaufnahme, vier parallele Abschnittsabfragen und ein gemeinsames Limit von 30 HTTP-Anfragen pro Minute.

Die nächste Optimierung soll die Laufzeit verkürzen, ohne Belege, fachliche Aussagekraft oder die Kompatibilität der Exporte zu verlieren. Dafür wurden zwei Änderungen vorgeschlagen:

1. Optionale JSON-Felder ohne Inhalt in der Modellantwort weglassen.
2. Ein alternatives Blablador-Modell unter vergleichbaren Bedingungen testen.

**Diese beiden Verbesserungen wurden bisher weder implementiert noch vermessen. Dieses Dokument enthält die bisherigen Messwerte, den vereinbarten Ansatz und den vorgesehenen Testplan. Es gibt noch keinen nachgewiesenen Geschwindigkeitsgewinn durch diese Änderungen.**

## Was bereits vorliegt

| Bestandteil | Stand |
|---|---|
| PDF-Parsing mit feiner Ontologie | Implementiert |
| Mehrere PDFs aus einem Eingabeordner | Implementiert in Batch-Version 1.0.0 |
| Vier parallele Abschnittsabfragen und gemeinsamer Limiter | Implementiert |
| Dokumentbezogene Event- und Beziehungs-IDs | Implementiert in Batch-Version 1.0.0 |
| Feste Paketversionen und Paketprüfsummen | Im ZIP enthalten |
| Offline-Tests der Batch-Version | 13 Tests bestanden, auch nach Entpacken des ZIP |
| Gezielt kompakte Modellantwort ohne leere optionale Felder | Vorgeschlagen; noch nicht umgesetzt |
| Vergleich mit einem alternativen Modell | Vorgeschlagen; noch nicht durchgeführt |

Die 13 Offline-Tests simulieren die API. Sie prüfen technische Eigenschaften und sind kein Geschwindigkeits- oder Qualitätsvergleich realer Modelle.

# Bisherige Messwerte

Die folgenden Werte stammen aus dem abgeschlossenen Vergleichslauf mit der Bode-PDF und **beiden Ontologien**. Sie sind keine isolierte Messung des neuen Batch-Parsers und kein Benchmark ausschließlich der feinen Ontologie.

Verwendete Modellbasis laut gespeichertem API-Katalog: `nvidia/Qwen3.8-Flash-Next-NVFP4`. Thinking war ausgeschaltet; das maximale Antwortbudget betrug 16.000 Tokens.

| Messgröße | Ergebnis | Bezugsmenge |
|---|---:|---|
| HTTP-Anfragen insgesamt | 258 | Gesamter Vergleich inklusive Wiederholungen, Modellabfragen und Nachbearbeitung |
| HTTP-Anfragen in der parallelisierten Hauptsitzung | 185 | Davon 184 Chat-Anfragen |
| Durchschnittliche Antwortzeit | 113,7 Sekunden | 184 abgeschlossene Chat-Anfragen dieser Sitzung |
| Median der Antwortzeit | 106,2 Sekunden | Dieselben Chat-Anfragen |
| Durchschnittliche Eingabegröße | ca. 16.148 Tokens | Dieselben Chat-Anfragen |
| Durchschnittliche Ausgabegröße | ca. 7.617 Tokens | Dieselben Chat-Anfragen |
| Antworten mit ausgeschöpftem 16.000-Token-Budget | 7 | Dieselben Chat-Anfragen |
| Anfragestarts je Minute mit mindestens einem Start | ca. 2,1 | Parallelisierte Hauptsitzung; Minuten ohne Start ausgeschlossen |
| Maximum je Kalenderminute | 5 | Gesamter Vergleich |
| Maximum im gleitenden 60-Sekunden-Fenster | 6 | Gesamter Vergleich |
| HTTP-429-Antworten | 0 | Gesamter Vergleich |

Die protokollierten durchschnittlich 1,2 Anfragen pro Minute über den gesamten Vergleich beziehen auch serielle Phasen und protokollierte Minuten ohne Anfrage ein. Diese Zahl sollte nicht als Durchsatz der vier parallelen Worker verwendet werden.

## Interpretation

Bei vier dauerhaft belegten Workern und unveränderter mittlerer Antwortzeit ergibt sich näherungsweise:

```text
4 × 60 / 113,7 ≈ 2,1 abgeschlossene Anfragen pro Minute
```

Das ist eine Abschätzung, keine Leistungszusage. Warteschlangen, wechselnde Abschnittsgrößen, Kontextabfragen und nicht vollständig belegte Worker beeinflussen die tatsächliche Gesamtdauer.

Das 30-RPM-Limit wurde im bisherigen Lauf nicht ausgeschöpft. Lange Anfragen und umfangreiche Antworten erklären einen wesentlichen Teil der Laufzeit. Aus den Logs lässt sich die Dauer von serverseitiger Warteschlange, Prompt-Verarbeitung und Token-Generierung nicht getrennt bestimmen.

Die finale fachliche Ausgabe umfasste 473 Events und 21 Beziehungen für die grobe sowie 1.121 Events und 43 Beziehungen für die feine Ontologie. Diese Zeilenzahlen allein sind kein Qualitätsmaß. Alle exportierten Datensätze blieben prüfbedürftig; unter anderem benötigen selbstreferenzierende Beziehungen und verworfene Kandidaten eine fachliche Betrachtung.

# Verbesserung 1: Kompakte JSON-Antworten

## Prinzip

Die Modellantwort soll nur Pflichtfelder und tatsächlich belegte optionale Angaben enthalten. Das Schema bleibt definiert und streng validiert. Es werden keine beliebigen neuen Feldnamen zugelassen.

Eine kürzere Darstellung muss bereits **vom Modell erzeugt werden**. Erst nach Empfang leere Felder aus dem JSON zu entfernen spart keine Generierungszeit und keine bereits erzeugten Tokens.

Illustratives Beispiel für einen Ausschnitt einer Modellantwort:

```json
{
  "event_id": "e1",
  "statement": "Der Abfluss sank.",
  "time_original": null,
  "time_normalized": null,
  "participants": null,
  "measurement_context": null
}
```

Kompakte Darstellung desselben Ausschnitts:

```json
{
  "event_id": "e1",
  "statement": "Der Abfluss sank."
}
```

Die Beispiele zeigen nur den Unterschied beim Weglassen leerer Felder. Sie sind **keine vollständigen gültigen Event-Datensätze**: Typ, Aussageart, Polarität und Belege müssen gemäß Schema ebenfalls vorhanden sein.

## Verbindliche Regeln für eine Umsetzung

- Pflichtfelder, Event-IDs, Beziehungsreferenzen und erforderliche Belege bleiben verpflichtend.
- Optionale Felder dürfen nur dann fehlen, wenn ihre Abwesenheit im Schema eine definierte Bedeutung hat.
- `0`, `false`, Verneinungen und gültige Statuswerte dürfen nicht als leer entfernt werden.
- „Im Dokument ausdrücklich unbekannt“ und „im Dokument nicht angegeben“ bleiben unterscheidbar. Explizite Angaben dürfen nicht durch einen Standardwert ersetzt werden.
- Leere Listen und Objekte werden nur weggelassen, wenn dies ausdrücklich dieselbe Bedeutung wie Abwesenheit hat. Keine pauschale Entfernung durch eine Wahrheitswertprüfung.
- Fehlende optionale Felder werden nach der Validierung lokal auf die vorgesehene interne Struktur abgebildet, beispielsweise mit `None` oder einer leeren Liste. Dabei entstehen keine neuen fachlichen Fakten.
- Rohantwort, normalisierte Daten und endgültiger Export bleiben unterscheidbar und nachvollziehbar.

Die bestehenden Pydantic-Modelle erlauben bereits für viele optionale Felder eine Auslassung. Zu prüfen ist insbesondere, wie Prompt und an die API übermitteltes JSON-Schema diese Möglichkeit vermitteln. Eine zusätzliche lokale Normalisierung muss nur implementiert werden, soweit sie nicht bereits durch die Modellvalidierung erfolgt.

## Unveränderte Ergebnisse für nachgelagerte Systeme

Die Excel-Exporte behalten die vorgegebenen **28 Event-Spalten und 30 Beziehungsspalten**. Auch Event-Referenzen, Dokumentherkunft, Zitate, Ontologieprüfung und Prüflisten bleiben erhalten. Die Änderung betrifft zunächst die Darstellung der API-Antwort, nicht das fachliche Zielschema.

Erwartung: Weniger ausgegebene Tokens können die Antwortzeit senken. Die tatsächliche Einsparung und mögliche Auswirkungen auf die Faktenabdeckung müssen gemessen werden.

**Nicht als Ersatz geeignet:** Nur `max-output-tokens` reduzieren. Das bisherige Budget wurde bereits mehrfach ausgeschöpft; ein niedrigeres Limit kann zusätzliche abgeschnittene Antworten, Teilungen und Wiederholungen verursachen.

# Verbesserung 2: Ein alternatives Modell testen

Als Kandidaten wurden GPT-OSS-120B (`alias-fast`) und Qwen3.8-27B diskutiert. Diese Namen sind Vorschläge für einen Vergleich, keine nachgewiesen schnelleren oder qualitativ gleichwertigen Alternativen für diesen Anwendungsfall.

Vor dem Test ist der aktuelle `/models`-Katalog abzurufen. Eine konkrete verfügbare Modell-ID und ihre Modellbasis werden im Testmanifest gespeichert. Aliasnamen können im Zeitverlauf auf andere Modelle zeigen. Für jedes Modell müssen JSON-Ausgabe, Kontextbudget und Thinking-Einstellungen separat auf Kompatibilität geprüft werden. Der bisherige Qwen-Thinking-Schalter ist nicht automatisch auf ein anderes Modell übertragbar.

Das bisherige Modell bleibt die Referenz. Ein anderer Modellstand erhält einen getrennten Laufordner; vorhandene Checkpoints verschiedener Modelle oder Prompts werden nicht gemischt.

# Geplanter Vergleich

| Test | Modell | Ausgabevertrag | Zweck |
|---|---|---|---|
| Referenz | Bisheriges Modell | Bisherige Vorgaben | Vergleichsbasis unter den Testbedingungen |
| A | Bisheriges Modell | Leere optionale Felder weglassen | Effekt des kompakteren JSON isolieren |
| B | Ein alternatives Modell | Derselbe kompakte Ausgabevertrag wie A | Effekt des Modellwechsels gegenüber A prüfen |

Für alle drei Tests sollen dieselben 10–15 repräsentativen PDF-Abschnitte mit derselben feinen Ontologie und demselben fachlichen Zielschema verwendet werden. Die Auswahl sollte Fließtext, dichte Tabellen, Messwerte, Maßnahmen, Verneinungen und Beziehungen enthalten.

Workerzahl und RPM-Limit bleiben zwischen den Varianten gleich und müssen innerhalb der bestätigten Betreibergrenzen liegen. Antwortbudget und Chunk-Aufbereitung werden möglichst konstant gehalten. Erforderliche modellabhängige Abweichungen sind zu dokumentieren. Neue Testläufe dürfen nicht aus vorhandenen Antwort-Checkpoints bedient werden; ansonsten würde die Cache-Nutzung statt der Modellgeschwindigkeit gemessen.

Da Serverlast schwankt, sollten die Varianten nach Möglichkeit in wechselnder Reihenfolge mehrfach auf derselben Stichprobe laufen. Mindestens sind Testzeitraum und Anzahl der Wiederholungen auszuweisen. Aus dem kleinen Vergleich darf nicht ungeprüft eine garantierte Gesamtlaufzeit für große PDF-Sammlungen abgeleitet werden.

## Zu messende Größen

| Bereich | Messgröße |
|---|---|
| Geschwindigkeit | Gesamtdauer der Stichprobe, Median und hohe Perzentile der Antwortzeit |
| Arbeitsmenge | Eingabe-/Ausgabetokens, alle HTTP-Versuche einschließlich Reparaturen und Wiederholungen |
| Robustheit | Schemafehler, abgeschnittene Antworten, Teilungen, Timeouts und HTTP-429-Antworten |
| Faktenqualität | Manuell geprüfte korrekte, fehlende und unbelegte Sachverhalte auf denselben Abschnitten |
| Beziehungen | Richtige Quell-/Zielzuordnung, Belegtreue, Verneinung, Bedingungen und Selbstreferenzen |
| Kompatibilität | Unveränderte Exportspalten und auflösbare Event-Referenzen |

Maßgeblich ist die Zeit für eine vergleichbare Menge **fachlich brauchbarer Ergebnisse**, nicht allein die Zahl der Anfragen oder ausgegebenen Events. Mehr JSONL-Zeilen und weniger verworfene Kandidaten beweisen für sich genommen keine höhere Qualität. Akzeptable Qualitätseinbußen beziehungsweise erforderliche Mindestqualität sollten vor dem Vergleich festgelegt werden.

# Parallelisierung: offene Randbedingung

Das vorhandene Skript unterstützt bis zu acht Worker. Bei unveränderter mittlerer Antwortzeit wären acht Worker rechnerisch etwa doppelt so schnell wie vier. Das wurde nicht vermessen und setzt ausreichende Serverkapazität voraus.

Eine RPM-Quote ist keine eigenständige Zusage einer bestimmten Anzahl gleichzeitiger Anfragen. Eine Betreiber-Mitteilung vom März 2026 bezeichnet parallele API-Anfragen als unerwünscht; die API-Dokumentation beschreibt modell- und nutzerabhängige Grenzen. Die zulässige Parallelität für das konkrete Konto und Modell ist daher vor einer Erhöhung zu klären. Diese Feststellung schränkt die frühere, rein technische Empfehlung zur Erhöhung der Workerzahl ein.

# Hinweise für die technische Umsetzung und LLM-gestützte Weiterarbeit

Dieser Abschnitt beschreibt einen Arbeitsauftrag für eine spätere Umsetzung; er besagt nicht, dass diese bereits erfolgt ist.

1. Von `SOLVE_Parsing_Fein_Batch_1.0.0.zip` ausgehen und eine neue Version erstellen. Den unveränderten Referenzstand behalten.
2. In `schemas.py` optionale Felder und vorhandene Defaults inventarisieren. In `extract_facts.py` die Promptvorgaben zur kompakten Ausgabe präzisieren. Die an Blablador übermittelte Schemaform in `blablador_client.py` prüfen.
3. Belegprüfung, Ontologieregeln und Referenzauflösung in `validate_export.py` beibehalten. Keine Absenkung dieser Prüfungen zur Verbesserung von Laufzeit oder Erfolgsquote.
4. Tests ergänzen: ausgelassene optionale Felder; fehlende Pflichtfelder; unbekannte Felder; Erhalt von `0`, `false` und expliziten Statuswerten; gleiche normalisierte Bedeutung kompakter und ausführlicher Antworten; gleiche Exportspalten und gültige Beziehungsreferenzen.
5. Prompt-/Codeversion und Testkonfiguration nachvollziehbar versionieren. Bei Änderungen neue Ergebnisordner und passende Cache-Schlüssel verwenden. Fingerprint-Schutz nicht umgehen.
6. Referenz, A und B auf derselben Stichprobe ausführen. API-Key geheim halten und jeden HTTP-Versuch über den gemeinsamen Limiter protokollieren.
7. Messergebnisse und fachliche Prüfung getrennt dokumentieren. Nur tatsächlich gemessene Verbesserungen als Ergebnis berichten. Bestehende historische Laufzeiten nicht als Messung der neuen Varianten darstellen.

# Quellen und zugehörige Dateien

Projektinterne Messgrundlage:

- `parsing_tests/results/volltest_20260923_ohne_thinking/logs/api_requests.jsonl`
- `parsing_tests/results/volltest_20260923_ohne_thinking/logs/api_requests_per_minute.csv`
- `parsing_tests/results/volltest_20260923_ohne_thinking/models.json`
- `parsing_tests/results/volltest_20260923_ohne_thinking/final_verification.json`
- `weitergabe/SOLVE_Parsing_Fein_Batch_1.0.0.zip` – vorhandener Softwarestand vor dieser Optimierung

Die internen Pfade dienen zur Nachprüfung im SOLVE-Projekt. Die Rohlogs sind nicht Bestandteil dieses Markdown-Dokuments. Die hier angegebenen Modellnamen und Messwerte ersetzen keine aktuelle Verfügbarkeitsprüfung.

Externe Betreiberquellen, zuletzt für diese Diskussion am 28.09.2026 konsultiert:

- [Blablador API-Zugang und Limits](https://sdlaml.pages.jsc.fz-juelich.de/ai/guides/blablador_api_access/)
- [Blablador-Modellkatalog](https://sdlaml.pages.jsc.fz-juelich.de/ai/guides/blablador_models/)
- [Blablador-Betreiber-Mitteilungen vom März 2026](https://lists.fz-juelich.de/hyperkitty/list/blablador-news%40lists.fz-juelich.de/2026/3/)

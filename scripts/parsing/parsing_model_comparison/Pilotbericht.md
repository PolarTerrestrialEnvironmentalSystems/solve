# Ergebnis des Parsing-Piloten vom 28.09.2026

**Kompakte Antworten mit dem bisherigen Qwen-Modell sind vielversprechend. GPT-OSS-120B ist mit dem getesteten Prompt kein geeigneter Ersatz. Eine produktive Umstellung ist durch diesen Pilot nicht freigegeben.**

Die angegebene PDF wurde gefunden: Die tatsächlichen Ordner heißen
`SOLVE_multisite_code_v1_1_0` und `dossier_33_seen_serper_v1_6_2_20260925`.
Es handelt sich um das sechsseitige Kennblatt B10 „Zufluss zur Rappbodetalsperre“,
Bearbeitungsstand 08.07.2025. Der API-Schlüssel wurde ausschließlich aus der vom
Nutzer benannten `.env` in den Prozessspeicher geladen.

## Vergleich und Messwerte

Drei identische kleine Ausschnitte, je ein Durchlauf pro Variante, eine Anfrage
gleichzeitig, 30 RPM und 16.000 Ausgabetokens. Ausgewählt wurden Maßnahmen und
Verneinungen auf Seite 2, die vermutete Kyrill-Wirkung auf Seite 3 sowie die neun
Sommerzuflusswerte 2016–2024 einschließlich der leeren Spalten 2025–2030 auf Seite 5.
Die Reihenfolge wechselte je Abschnitt. Der endgültige Live-Vergleich lief am
28.09.2026 ungefähr von 15:44 bis 15:52 Uhr MESZ. PDF-Aufbereitung und
Umgebungseinrichtung sind nicht in den folgenden Extraktionszeiten enthalten.

| Messgröße | Referenz: Qwen bisher | A: Qwen kompakt | B: GPT-OSS kompakt |
|---|---:|---:|---:|
| Summe Extraktionszeit | 155,3 s | 131,0 s | 185,8 s |
| Maßnahmenabschnitt | 80,6 s | 76,5 s | 22,0 s |
| Unsicherheitsabschnitt | 56,9 s | 37,0 s | 120,4 s |
| Tabellenabschnitt | 17,8 s | 17,5 s | 43,4 s |
| Eingabetokens laut API | 42.784 | 43.255 | 43.365 |
| Ausgabetokens laut API | 8.262 | 6.260 | 24.693 |
| Abgeschlossene HTTP-Versuche | 3 | 3 | 3 |
| Schema-gültige Abschnittsantworten | 3/3 | 3/3 | 2/3 |
| Abgeschnittene Antworten | 0 | 0 | 1 |
| Exportierte Events / Beziehungen | 10 / 2 | 13 / 3 | 0 / 0 |
| Verworfene Kandidaten aus gültigem JSON | 1 | 0 | 16 |
| Nicht auflösbare Referenzen im finalen Export | 0 | 0 | 0 |
| Selbstbeziehungen im finalen Export | 0 | 1 | 0 |

Alle neun HTTP-Anfragen lieferten Status 200; es gab keine HTTP-429-Antwort,
keine HTTP-Wiederholung und keine JSON-Reparatur. Der GPT-OSS-Unsicherheitsabschnitt
endete mit `finish_reason=length`; der kurze einzelne Textblock ließ sich nach den
vorhandenen Teilungsregeln nicht weiter teilen und wurde als fehlgeschlagen erfasst.
Die Null Referenzen bei GPT-OSS ist wegen des leeren Exports kein Qualitätsnachweis.

Qwen kompakt benötigte **15,6 % weniger Zeit und 24,2 % weniger Ausgabetokens**.
In seinen drei Rohantworten gab es keine Nullfelder; die Referenz enthielt 173.
Die Aussagen und ihre Detailstruktur variierten zwischen den unabhängigen
Modellantworten. Diese Werte belegen daher keine generell verlustfreie Beschleunigung.
Mit drei Abschnitten und einer Wiederholung sind weder stabile Geschwindigkeitsfaktoren
noch belastbare hohe Perzentile oder eine Gesamtlaufzeit für große PDFs ableitbar.

Modellbasen laut gespeicherten Live-Katalogen:

- Referenz und A: `nvidia/Qwen3.8-Flash-Next-NVFP4`, konkret angefragt als
  `02 - Qwen3.8-Flash-Next-NVFP4, general purpose large model`; Thinking aus.
- B: `openai/gpt-oss-120b`, konkret angefragt als
  `01 - GPT-OSS-120b - an open model released by OpenAI in August 2025`;
  kein Qwen-spezifischer Thinking-Schalter, Servervorgabe. Der Unterschied ist
  dokumentiert; dies ist kein Vergleich bei nachweislich identischem Thinking-Modus.

Alle Antworten verwendeten das API-Format `json_schema`. Schema, Ontologie,
Belegprüfung und Exportregeln wurden nicht abgeschwächt.

## Fachliche Stichprobenprüfung

Die relevanten PDF-Seiten 2, 3 und 5 wurden visuell geprüft und mit den gespeicherten
Quelltexten und Ergebnissen verglichen. Dies ist eine begrenzte Sichtprüfung,
keine vollständige Goldannotation oder unabhängige fachliche Freigabe.

| Prüfpunkt | Referenz | Qwen kompakt | GPT-OSS kompakt |
|---|---|---|---|
| Keine Einschränkungen von Rohwasserbereitstellung und Niedrigwasseraufhöhung | Nicht als Event erfasst | Mit `polarity=negated` erfasst | Nicht als Event erfasst |
| Mindeststauinhalt 60 Mio. m³ | Kandidat vorhanden, Beleg über Blockgrenze falsch zugeordnet und verworfen | Mit zwei passenden Belegstellen exportiert | Kandidat vorhanden, falsche Themen-ID und Belegzuordnung; verworfen |
| Vorziehen des Sommerstauziels und Erreichen im März | Erfasst; Schneerücklage nur im Beleg | Erfasst, Schneerücklage auch in Aussage | Kandidat vorhanden, verworfen |
| Kyrill-Wirkung bleibt Vermutung | Event und Beziehung `suspected` | Event und Beziehung `suspected` | Antwort abgeschnitten; kein verwertbarer Abschnitt |
| Neun Sommerwerte, korrekte Zuordnung 2016–2024 | Alle neun im Aussagefeld; Werte separat ohne Jahresfeld | Alle neun mit Jahreszuordnung in einer `value_original`-Zeichenkette | Neun Kandidaten mit richtigen Aussagen, alle wegen falscher Themen-IDs verworfen |
| Leere Jahre 2025–2030 | Keine Messwerte erfunden | Keine Messwerte erfunden; Leerstellen ausdrücklich vermerkt | Keine Messwerte erfunden |

Geprüfte Sommerwerte in Mio. m³: 2016: 6; 2017: 26; 2018: 1; 2019: 5;
2020: 6; 2021: 9; 2022: 1; 2023: 14; 2024: 12.

**Zusätzliche Qualitätsprobleme:**

- Qwen kompakt erzeugte im Maßnahmenabschnitt eine `causes`-Beziehung von `e7`
  auf `e7`. Trockenheit und Maßnahme wurden nicht in zwei geeignete Events
  getrennt. Die bestehenden Prüfungen lassen diese Selbstbeziehung im Export.
- Qwen kompakt präzisierte `Sommer 2007` beziehungsweise `Jahr 2007` ohne Beleg zu
  `2007-05-01` bis `2007-09-30`. Diese Tage standen nicht in den übermittelten
  Textspannen. Für das hydrologische Sommerhalbjahr nennt die vollständige PDF
  zudem den 31. Oktober als Ende. Auch `Januar 2007` wurde unnötig auf Tagesgrenzen
  präzisiert. Diese gültigen Datumsstrings passieren die technische Validierung.
- Im kompakten 60-Mio.-Event bleibt „bis zum Jahresende“ in der Aussage erhalten,
  fehlt aber im strukturierten Zeitfeld. Weniger Nullfelder garantieren also nicht,
  dass belegte Informationen in allen vorgesehenen Detailfeldern stehen.
- Beide Qwen-Läufe bündeln die neun Tabellenmessungen in einem Event. Die Werte
  sind inhaltlich vorhanden, aber nicht als neun getrennte Jahr-Wert-Datensätze.
- GPT-OSS verwendete freie Kategorien wie `Management` und `Hydrologie` statt der
  zulässigen `topic:...`-IDs. Alle 14 Events und zwei Beziehungen aus seinen beiden
  vollständig lesbaren Antworten wurden verworfen. Bei den Tabellenkandidaten
  fehlen zudem die Einheiten im strukturierten Wertefeld, obwohl sie im Satz stehen.
  Der abgeschnittene Abschnitt produzierte viele wiederholte Beziehungen und
  verkürzte Zitate mit Auslassungspunkten.

Die größeren Eventzahlen sind **kein** Qualitätsmaß. Insbesondere wären ein
automatisches Ersetzen ungültiger Themen oder eine Lockerung der Belegprüfung
keine vertretbare Abkürzung, um GPT-OSS scheinbar erfolgreich zu machen.

## Umsetzung und Tests

In `compact/extract_facts.py` wurden die Vorgaben „alle Felder ausfüllen“ durch
gezieltes Weglassen unbelegter optionaler Felder mit Default `null` ersetzt.
Statusfelder, Pflichtfelder, Belege, Null als explizites Ontologie-Signal, `0`,
`false`, Verneinungen sowie leere Listen und Objekte bleiben ausdrücklich erhalten.
Die Versionskennung wurde auf `1.0.1-compact-pilot` gesetzt. Das Schema selbst
brauchte keine Änderung: Pydantic ergänzt die vorhandenen Defaults bereits lokal.

- Unveränderte Referenz: 13 Offline-Tests bestanden, 19 Original-Paketdateien anhand
  ihrer mitgelieferten Prüfsummen verifiziert.
- Kompakte Testkopie: 18 Offline-Tests bestanden, darunter fünf zusätzliche Tests
  zu optionalen und verpflichtenden Feldern, unbekannten Feldern, Null/Nullwert-
  Unterscheidung, Statuswerten, Referenzen und unveränderten Exportspalten.
- Python 3.14.7 und alle Paketversionen aus `requirements.lock.txt` verwendet.
  Die erste Prüfung mit dem gebündelten Runtime scheiterte erwartungsgemäß an
  abweichenden Paketversionen. Eine getrennte passende Umgebung wurde angelegt.
- Ein bestehender Test scheiterte zunächst sowohl in Referenz als auch Compact
  wegen des Vergleichs von Windows-Kurzpfad und aufgelöstem Temporärpfad. Mit
  ausgeschriebenem temporärem Verzeichnis bestanden beide Suiten, ohne Codeänderung.
- Rohantworten, normalisierte Pakete, finale Exporte und verworfene Kandidaten
  bleiben getrennt gespeichert. API-Key und bestehende `.env` wurden nicht kopiert.

## Vorher-nachher-Beispiel und nächste Entscheidung

`JSON_vorher_nachher.md` zeigt eine tatsächliche Aussage aus PDF-Seite 2. Das
isolierte Formatbeispiel enthält zweimal denselben Datensatz, einmal ausführlich,
einmal ohne optionale Nullfelder. Die gleiche normalisierte Bedeutung ist geprüft.
`actual_compact_qwen_event.json` enthält daneben die echte kompakte Modellantwort
zu dieser Aussage. Die unveränderten Excel-Spalten bleiben 28 / 30.

**Empfehlung:** Beim bisherigen Modell bleiben und die kompakte Variante weiter
prüfen. Vor Übernahme sollten insbesondere Datumspräzision, vollständige Belegung
bekannter Detailfelder und Selbstbeziehungen abgesichert werden. Anschließend auf
einer größeren Stichprobe wiederholt testen. GPT-OSS mit den hier getesteten
Einstellungen nicht als Ersatz einsetzen. Das Original auf Y: wurde nicht verändert.

Die abgebrochene breitere Kalibrierung steht unter `calibration_results`; ihre
16.000 Ausgabetokens und rund 287 Sekunden gehören ausdrücklich nicht zu obiger
Vergleichstabelle. Weitere Konfiguration, Logs und Einschränkungen: `README.md`,
`metrics.json`, `quality_details.txt` und die Manifeste unter `results/`.

# Wiederholungstest: Selbstbeziehungen mit Qwen

Fragestellung: Treten Selbstbeziehungen bei wiederholter Extraktion mit dem
bisherigen oder dem kompakten JSON-Prompt häufiger auf?

Vorab festgelegtes Design: drei kausale Textausschnitte aus der Nutzer-PDF
`1bfed581c2f2d2d5047c584035c526a2eb9c75315447f09b9d482f053671f44d.pdf`,
je drei Wiederholungen mit beiden Qwen-Prompts, insgesamt 18 Einzelläufe.
Die PDF hat 40 Seiten. Diagramme und gescannte Anhänge sind nicht Teil des Tests.

Auswahl vor Beginn der Antworten:

1. Seite 10: Talsperrenwirkung auf den QM364-Abfluss der Moldau (20,0 auf 38,8 m³/s).
2. Seite 11: Eger-Talsperren und ihr Einfluss auf die Niedrigwasseraufhöhung von Eger und Elbe.
3. Seite 17: Verlagerung der Niederschläge, Winterhochwasser und sommerliches Niedrigwasser.

Die Seiten wurden visuell kontrolliert. Die Originaltextspannen aus der vorhandenen
PDF-Aufbereitung stehen unverändert in `study_plan.json`. Zwei Quellblöcke bleiben
bei der Passage auf Seite 17 getrennt; die Belegzuordnung muss dies berücksichtigen.
Es werden keine Dokumentkontextblöcke ergänzt. Die Auswahl ist gezielt auf
Kausalaussagen ausgerichtet und nicht repräsentativ für sämtliche Dokumentabschnitte.

Modellbasis `nvidia/Qwen3.8-Flash-Next-NVFP4`, konkret angefragte Modell-ID aus dem
frischen Live-Katalog in `models.json`. Temperatur 0, Thinking aus, 16.000
Ausgabetokens, ein Worker, gemeinsame Begrenzung auf 30 HTTP-Anfragen/Minute,
300 Sekunden Timeout, maximal ein HTTP-Retry. JSON-Schema, Ontologie und Export-
prüfungen sind identisch. Prompts stammen unverändert aus den zuvor getesteten
Kopien `../baseline` und `../compact`; keine Anti-Selbstbeziehungsregel wurde ergänzt.
Variantenreihenfolge wechselt nach Abschnitt und Wiederholung. Jeder Lauf hat einen
eigenen Ergebnisordner und eigene Checkpoints; keine Antworten aus früheren Läufen.
Reparaturen oder Teilungen werden als Teil des ursprünglichen Laufs mitgezählt.

Primäre Auswertung: Ein Lauf ist betroffen, wenn mindestens eine Beziehung
`source_event_id == target_event_id` enthält. Getrennt zählen wir schema-gültige
Modellausgaben und finale Exporte. Zusätzlich werden die betroffenen Kanten im
Verhältnis zu allen Kanten, fehlgeschlagene Läufe und Läufe ohne Beziehungen
ausgewiesen. Damit wird ein leerer Export nicht mit fehlerfreier Extraktion verwechselt.
Die konkreten Selbstbeziehungen werden anhand ihrer Events und Quelltexte betrachtet.

Die Wiederholungen sind keine unabhängigen Dokumente. Gleiche Ausgaben bei
Temperatur 0 können auftreten. Diese Untersuchung liefert eine beobachtete
Häufigkeit unter den Testbedingungen, keine allgemeine Fehlerwahrscheinlichkeit
und keinen Beweis einer ursächlichen Wirkung des kompakten JSON-Prompts.

Ausführung: `../.venv/Scripts/python.exe run_study.py` (vorhandene Laufordner werden
nicht wiederverwendet). Auswertung: `../.venv/Scripts/python.exe analyze_study.py`.
Python 3.14.7, gesperrte Paketversionen aus der bisherigen Testumgebung.

Ergebnisse: `summary.json`, `run_metrics.csv`, `self_relation_examples.json`,
alle Rohantworten und Exporte unter `results/`. Manifeste enthalten Code-, Prompt-,
Schema- und Ontologieprüfsummen. Der Schlüssel bleibt in der ursprünglichen lokalen
`.env` und wird ausschließlich im Speicher geladen.

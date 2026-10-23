# Wiederholungstest: Selbstbeziehungen in Qwen-Antworten

**Die Selbstbeziehung tritt auch mit dem bisherigen ausführlichen Prompt auf.**
In dieser neuen Stichprobe enthielten 4 von 9 bisherigen und 1 von 9 kompakten
Modellantworten mindestens eine Selbstbeziehung. Beide Varianten erzeugten
insgesamt je 17 Beziehungen vor der Exportprüfung. Hier war kompakt also nicht
häufiger betroffen. Das ist kein allgemeiner Überlegenheitsnachweis.

Getestet am 28.09.2026 mit der zweiten Nutzer-PDF: Bericht von Manfred Simon und
Jürgen Böhme zu vereinbarten minimalen mittleren Monatsabflüssen der Elbe am
Grenzprofil Hřensko/Schöna, März 2012. Keine Prüfung der heutigen hydrologischen
oder rechtlichen Gültigkeit der historischen Aussagen.

## Beobachtetes Ergebnis

| Kriterium | Bisheriger Prompt | Kompakter Prompt |
|---|---:|---:|
| Aufgezeichnete Läufe | 9 | 9 |
| Vollständig schema-gültige Läufe | 9 | 9 |
| Läufe mit Selbstbeziehung in schema-gültiger Modellantwort | 4/9 | 1/9 |
| Selbstbeziehungen / alle Beziehungen vor Exportprüfung | 4/17 | 1/17 |
| Läufe mit Selbstbeziehung im finalen Export | 0/9 | 0/9 |
| Selbstbeziehungen / alle exportierten Beziehungen | 0/0 | 0/0 |
| Läufe ohne Beziehung in Modellantwort | 0 | 0 |
| Läufe ohne exportierte Beziehung | 9 | 9 |
| HTTP-Versuche | 9 | 9 |
| Ausgabetokens laut API | 24090 | 18252 |
| Summe Extraktionsdauer | 336.1 s | 257.5 s |

Ein Lauf zählt bereits bei einer Selbstbeziehung als betroffen. Die Kantenanzahl
ist zusätzlich angegeben, damit unterschiedlich viele erzeugte Beziehungen nicht
verborgen bleiben. „Schema-gültig“ bedeutet nicht fachlich richtig oder exportierbar.

## Aufteilung nach Textausschnitt

| Ausschnitt | Bisherig: betroffene Modellantworten | Kompakt: betroffene Modellantworten | Bisherig: betroffene Exporte | Kompakt: betroffene Exporte |
|---|---:|---:|---:|---:|
| Moldau, Talsperrenwirkung (Seite 10) | 3/3 | 1/3 | 0/3 | 0/3 |
| Eger und Elbe, Niedrigwasseraufhöhung (Seite 11) | 1/3 | 0/3 | 0/3 | 0/3 |
| Saisonaler Niederschlag und Abfluss (Seite 17) | 0/3 | 0/3 | 0/3 | 0/3 |

## Testbedingungen und Aussagegrenzen

### Betrachtung der konkreten Fehler

Alle fünf beobachteten Selbstbeziehungen wurden anhand des referenzierten Events
und der Quellpassage betrachtet. Bei der Moldau enthält ein Event bereits die
Aussage, dass die Talsperrenbewirtschaftung den QM364-Abfluss von 20,0 auf 38,8 m³/s
erhöhte. Die Modelle erzeugten dazu `increases` beziehungsweise `causes` von diesem
Event auf sich selbst. Im zweiten bisherigen Eger-Lauf verweist eine `affects`-
Beziehung ebenfalls zweimal auf das Event, das bereits Talsperre und Abflusswirkung
zusammenfasst. Diese Kanten trennen Ursache und Wirkung nicht in geeignete Events.

Alle 18 Läufe lieferten schema-gültige Antworten; innerhalb jeder Text-/Prompt-
Kombination waren die drei normalisierten Antworten unterschiedlich. Dennoch
wurden in dieser Stichprobe **überhaupt keine Beziehungen final exportiert**.
Die bestehenden Prüfungen beanstandeten unter anderem freie Themenbezeichnungen
statt erlaubter Themen-IDs, ungültige Belege und dadurch nicht auflösbare
Event-Referenzen. Die Selbstbeziehungen verschwanden durch diese anderen Fehler,
nicht durch eine gezielte Selbstbeziehungsprüfung. Die Export-Nullwerte dürfen
deshalb nicht als fehlerfreie Extraktion interpretiert werden.

Empfehlung aus diesem Test: Die kompakte Ausgabe nicht wegen des einzelnen
Selbstbeziehungsfunds im ersten Pilot verwerfen. Eine gezielte Prüfung kausaler
Selbstbeziehungen ist für **beide** Varianten sinnvoll; auffällige Kanten sollten
in die Prüfliste oder in einen kontrollierten Reparaturschritt gehen. Zusätzlich
muss die Einhaltung der Ontologie-IDs verbessert werden. Weder Prompts noch
Validierung wurden während dieser Messreihe entsprechend verändert.

### Grenzen der Übertragbarkeit

Drei vorab ausgewählte kausale Passagen wurden je dreimal pro Variante verarbeitet
(18 Einzelläufe). Die Passagen stammen aus einer PDF, nicht aus 18 unabhängigen
Dokumenten. Textspannen, Modell, Schema, Ontologie und Einstellungen waren identisch.
Die Variantenreihenfolge wechselte; Antworten wurden nicht aus Checkpoints übernommen.
Keine Anti-Selbstbeziehungsregel wurde ergänzt und keine Exportprüfung gelockert.

Modellbasis `nvidia/Qwen3.8-Flash-Next-NVFP4`, Temperatur 0, Thinking aus,
16.000 Ausgabetokens, eine Anfrage gleichzeitig, gemeinsamer 30-RPM-Limiter.
Die genaue Modell-ID und alle Prüfsummen stehen in den Laufmanifesten.
Python 3.14.7 mit den gesperrten Paketversionen des ersten Tests.

Wiederholungen können bei Temperatur 0 gleich ausfallen. Die Anzahl unterschiedlicher
normalisierter Antworten pro Text und Variante steht in `summary.json`. Daher
stellen die Häufigkeiten nur Beobachtungen dieser gezielten Stichprobe dar; daraus
folgt keine allgemeine Fehlerwahrscheinlichkeit oder statistisch abgesicherte
Überlegenheit einer Promptvariante. Der erste Pilot wird nicht mit diesen Zahlen
zusammengerechnet, weil andere Dokumente und Ausschnitte verwendet wurden.

Die Modellstufe zählt Beziehungen aus erfolgreich validierten Extraktionspaketen.
Rohantworten vor etwaiger Reparatur oder abgebrochene Antworten bleiben separat
gespeichert. Die endgültige Exportstufe kann Beziehungen zusätzlich verlieren,
wenn Events, Themen-IDs oder Belege ungültig sind. Ein leerer Export belegt deshalb
keine erfolgreiche Behandlung von Selbstbeziehungen.

## Dateien

- `study_plan.json`: vorab festgelegte Textspannen und Auswertungseinheit.
- `summary.json` und `run_metrics.csv`: aggregierte Zahlen und alle Einzelläufe.
- `self_relation_examples.json`: betroffene Beziehungen und zugehörige Events.
- `review_reason_counts.json`: Gründe der bestehenden Qualitätsprüfungen.
- `results/`: Rohantworten, Checkpoints, normalisierte Pakete, Exporte, Prüflisten.
- `final_verification.json`: gleiche Eingaben, stabile Prompts, neu gezählte
  Selbstbeziehungen, Referenz- und Excel-Prüfung sowie Geheimnisscan bestätigt.

Das Original auf Y: sowie beide getesteten Prompts blieben unverändert.

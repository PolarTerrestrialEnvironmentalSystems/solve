# Parser 1.2.0: Absätze erhalten und Vergleichsangaben verbessern

Stand: 29.09.2026. Umsetzung und Vergleich mit Version 1.1.0 anhand derselben PDF-Seiten 10, 11 und 17.

## Was geändert wurde

- 4.000 Zeichen als weiche Zielgröße: Den Absatz bzw. die Tabellenzeile vollständig übernehmen, der/die die Zielgröße erreicht oder überschreitet.
- Keine künstliche Zerlegung nach 1.600 Zeichen oder halber Abschnittsgröße bei der Aufbereitung. Im Test: 12 statt 18 Blöcke; normalisierter Quelltext identisch.
- Tatsächliche Abschnittsgrößen im Test: 4.306 und 3.446 Zeichen. Auch die technische Wiederherstellung teilt keinen einzelnen Absatzblock; bei unüberwindbaren Grenzen wird ein expliziter Fehler ausgewiesen.
- Optionale Zeitangaben je Wert: values[].time_original und values[].time_normalized. Hauptspalten der Excel-Exporte bleiben gleich.
- Zulässige Themen-/Klassen-/Beziehungstyp-IDs im API-Schema; lokale Validierung bleibt erhalten.
- Klarere Belegregeln und eine begrenzte Qualitätsreparatur für ungültige Belege/IDs sowie erkennbare unvollständige Zahlenvergleiche. Vorhandene Fachinhalte werden geschützt; kein vollständiger semantischer Vollständigkeitsnachweis.

## Messungen

| Version | Lauf | Zeit (min:s) | API-Aufrufe | Exportierte Fakten / Beziehungen | Vollständige Prüfpunkte / 19 | Teilweise |
|---|---:|---:|---:|---:|---:|---:|
| 1.1.0, bisherige 4.000-Zeichen-Aufteilung | 1 | 3:29 | 3 | 15 / 1 | 8 | 1 |
| 1.1.0, bisherige 4.000-Zeichen-Aufteilung | 2 | 3:36 | 3 | 17 / 0 | 6 | 3 |
| 1.2.0, ganze Absätze | 1 | 1:19 | 2 | 13 / 3 | 8 | 1 |
| 1.2.0, ganze Absätze | 2 | 3:03 | 4 | 11 / 1 | 6 | 1 |

Mittlere Laufzeit bisher: **3:32**, neue Version: **2:11**. Dies sind Beobachtungen dieses kleinen Piloten, keine belastbare allgemeine Beschleunigungsrate.

Alle Zeiten umfassen Extraktion, zusätzliche Reparaturen, Validierung und Export. Gleiche Qwen-Modellkennung, Temperatur 0, Thinking aus, 16.000 Ausgabetokens, ein Worker, 30 RPM. Keine Wiederverwendung alter Antworten. PDF-Aufbereitung und Modellkatalogabfrage sind ausgeschlossen.

## Qualität und Grenzen

**Ergebnis:** Die geprüfte Gesamtvollständigkeit ist im kleinen Pilot unverändert: sechs bis acht von 19 Prüfpunkten vollständig, im Mittel sieben. Die Zuordnung der Moldau-Vergleichswerte zu ihren Zeiträumen verbessert sich konkret; der Eger-Vergleich gelingt im ersten Lauf, scheitert im zweiten erneut an Belegen. Die durchschnittlich kürzere beobachtete Laufzeit ist deshalb kein Beweis für einen allgemeinen Qualitätsgewinn.

Die 19 Prüfpunkte wurden aus dem vorigen Test unverändert übernommen. F = vollständig, P = teilweise, M = fehlt, E = wesentliche unbelegte Ergänzung. Angaben nur im Beleg zählen nicht als extrahiert; Wiederholungen erhöhen die Abdeckung nicht. Nicht verblindete Prüfung durch den Assistenten, kein unabhängiges Expertenrating.

Zulässige IDs und wörtliche Belege garantieren keine richtige Ontologieklasse oder kausale Interpretation. Es bleiben Auslassungen und teilweise Dubletten. Die kleine Vergleichsstichprobe umfasst ein Dokument; geänderte Aufbereitung, Prompt, Schema und Nachprüfung wirken gemeinsam. Die alten Läufe fanden früher statt, Serverlast kann die Laufzeiten beeinflussen. Ein allgemeiner Qualitäts- oder Geschwindigkeitsgewinn lässt sich daraus nicht ableiten.

### Einzelbewertungen

#### r1_chars4000

Prüfpunkte 1–19: F, F, M, M, F, F, M, F, M, M, F, M, F, F, M, M, M, M, P

- 1 und 8: Beide Vergleichswerte mit separaten korrekten Zeiträumen in values[].time_original/time_normalized erhalten. Eger-Vergleich besteht jetzt auch die Belegprüfung.
- 19: Prognosestatus und Sommerbezug vorhanden; die häufigere Unterschreitung gegenüber bisher wird in Fachfeldern nicht explizit übernommen.
- Auslassungen: Speicherdaten, Berounka-Anteil, Winter-/Sommer-Kausalketten, Flächenanteile und Bewirtschaftungskonflikt fehlen weiterhin.
- Dubletten: Die fortbestehende Grenzabflussvereinbarung wird in Events 9 und 13 wiederholt.
- Semantische Grenzen: fine:ConcentrationChange ist für die Abflussvergleiche fachlich unpassend, obwohl die ID zulässig ist. Die ersten beiden Beziehungen machen die jeweilige Mindestabgaberegel zur Ursache der historischen QM364-Erhöhung; die Quelle benennt allgemeiner die Talsperrenwirkung. Die Checklistenabdeckung bewertet die Sachverhalte, nicht eine Freigabe dieser Klassifikation/Kanten.

Reparaturstatus: {}
Ablehnungsgründe: []

#### r2_chars4000

Prüfpunkte 1–19: F, F, M, M, F, F, M, M, M, M, M, M, F, F, M, M, M, M, P

- 1 und 2: Moldau-Vergleich und 36,9-Wert behalten ihre Zeiträume in values[].time_original; time_normalized bleibt hier leer, die Original-Zuordnung bleibt dennoch erhalten.
- 8 und 11: Eger-Vergleich und Mindestabgabe werden wegen ungültiger Belege ausgesondert.
- 19: Prognose und Sommerbezug erhalten, Zunahme der Häufigkeit nicht explizit in Fachfeldern.
- Reparaturen: Qualitäts- und Selbstbeziehungsreparatur ändern unerlaubt Metadaten und werden verworfen. Originale bleiben erhalten.
- Sonstige Grenzen: Event 6 behauptet eine Veränderung am Pegel Děčín, obwohl die bereitgestellte Passage vor der eigentlichen Aussage abbricht. Belegprüfung allein erkennt das semantisch nicht. Auch hier ist fine:ConcentrationChange für Abflusswerte unpassend. Weitere Speicher-, Flächen- und Kausalangaben fehlen.

Reparaturstatus: {'source_quality_repair:rejected': 1, 'causal_self_relationship_repair:rejected': 1}
Ablehnungsgründe: ['quality_repair_changed_metadata', 'repair_changed_unaffected_metadata']

## Prüfung und Nachvollziehbarkeit

- 59 Offline-Tests erfolgreich: weiche Grenze, lange Absätze, Tabellenzeilen, ID-Enums, wertbezogene Zeiträume, Mehrblock-Belege, Reparaturgrenzen und bisherige Batch-/Selbstbeziehungsregressionen.
- Ein Fehler der ersten Vergleichserkennung wurde im Prototyp erkannt: Ein früheres Speichervolumen durfte nicht mit einem späteren Abflusswert verbunden werden. Die korrigierte Erkennung überschreitet weder einen neuen numerischen von-Ausdruck noch Satzgrenzen. Zwei Regressionstests sichern dies ab.
- Der unterbrochene Prototyp unter paragraph_quality_prototype ist nicht Teil der obigen Messungen. Alle gewerteten Abschlussläufe entstanden nach der Korrektur mit frischen Antworten.
- plan.json, source_equivalence.json, manifests, Rohantworten und Exporte liegen unter parsing_model_comparison/paragraph_quality_study. verified_summary.json und manual_review.json enthalten Messwerte und Einzelbewertungen.
- Beim Erstellen des Berichts wurden identischer normalisierter Quelltext, vollständige Übergabe aller Blöcke und unveränderter getesteter Python-Code geprüft.
- Alte Versionen und Ergebnisordner bleiben erhalten. Für Version 1.2.0 einen neuen Ergebnisordner verwenden.

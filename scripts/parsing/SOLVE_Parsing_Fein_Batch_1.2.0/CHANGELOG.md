# Version 1.2.0 – Ganze Absätze und gezielte Qualitätsprüfung

- 4.000 Zeichen als weiche Standard-Zielgröße in Einzel- und Batch-CLI.
- Vollständige Absatz-/Tabellenblöcke; keine künstliche Trennung nach Zeichenanzahl.
  Recovery teilt ausschließlich zwischen Blöcken. Neue Aufbereitungs-Fingerprints.
- Schema 1.1.0: optionale Zeitangaben je Quantity; unveränderte Excel-Hauptspalten.
- Ontologieabhängige ID-Enums im strukturierten API-Schema und Prompt-Fallback.
- Separate Belege für Aussagen über mehrere Blöcke; explizite Zuordnung von Vergleichswerten.
- Ein begrenzter Qualitätsreparaturversuch vor dem bestehenden Selbstbeziehungsschutz.
  Fachinhalte bestehender Einträge sind geschützt; neue Vergleichs-Events und Beleg-/ID-
  Korrekturen werden geprüft. Originalantwort bleibt bei Ablehnung erhalten.
- 59 Offline-Tests erfolgreich; Live-Vergleich auf den bisherigen PDF-Seiten im
  beigefügten Testbericht. Keine Garantie vollständiger oder fehlerfreier Extraktion.

# Version 1.1.0 – Schutz gegen kausale Selbstbeziehungen

Die neue Version verwendet die kompakte JSON-Ausgabe und verhindert, dass eine
kausale Kante ihr eigenes Event als Ursache und Wirkung exportiert.

Umgesetzt:

- Explizite Promptregel: Ursache und Wirkung müssen unterschiedliche belegte
  Events sein; keine bloßen Kopien mit neuen IDs.
- Höchstens ein semantischer Reparaturaufruf pro betroffenem Extraktionspaket.
  Neue Events sind nur zulässig, wenn sie von der reparierten Kante verwendet
  werden. Bereits vorhandene Fakten und unbetroffene Beziehungen bleiben erhalten.
- Annahme einer Reparatur nur nach Schema-, Beleg-, Ontologie-, Status- und
  Referenzprüfung. Eine Änderung des Beziehungstyps zur Umgehung der Prüfung
  oder die Aufwertung einer Vermutung wird abgewiesen.
- Harte Exportprüfung für `causes`, `contributes_to`, `affects`, `increases`,
  `decreases`, `prevents`, auch nach ID-Auflösung und Event-Zusammenführung.
- Vollständige Dokumentation der ursprünglichen Kante, Rohantworten,
  Reparaturentscheidung und etwaiger Rücknahme in Checkpoints und Prüflisten.
- Unveränderte 28 Event-/30 Beziehungsspalten. Bereits bestehende Qualitätsregeln
  und der gemeinsame API-Limiter gelten weiter.

Validiert am 28.09.2026:

- 41 Offline-Tests unter Python 3.14.7 mit den gesperrten Paketversionen.
- Wiedergabe von 19 gespeicherten Fällen: alle sechs bekannten kausalen
  Selbstbeziehungen explizit ausgesondert.
- Ein echter Reparaturtest mit Qwen und dem Sommerstauziel-Fall: Reparatur
  akzeptiert; im Export stehen zwei Beziehungen und keine Selbstbeziehung.
  Die reparierte Kante verbindet die Trockenheit im März/April mit der
  Vorverlegung des Sommerstauziels. Der API-Aufwand betrug eine Modellkatalog-
  Abfrage und eine Reparaturanfrage.
- Die historischen Fehlerfälle und die erfolgreiche Live-Reparatur sind als
  portable Fixtures in den Offline-Tests enthalten. Keine API-Schlüssel im Paket.

Die bisherigen Batchtests enthielten selbst eine kausale Selbstbeziehung als
Erfolgsfixture. Diese wurde durch zwei unterschiedliche Events mit einer
zeitlichen Beziehung ersetzt; ID-Trennung, Wiederaufnahme und Exportprüfung
werden weiterhin geprüft. Der temporäre Testpfad wird unter Windows aufgelöst,
damit 8.3-Kurzpfade keine falschen Pfadverletzungen auslösen.

Diese Prüfung garantiert keine allgemeine fachliche Fehlerfreiheit. Sie erkennt
identische Referenzen und zusammengeführte identische Events, aber nicht zuverlässig
jede paraphrasierte semantische Gleichheit. Neue zulässige Reparaturen bleiben
fachlich prüfbedürftig. Themen-ID-Fehler und unbelegte Datumspräzisierungen werden
nicht automatisch repariert. Ein einzelner erfolgreicher Live-Test misst keine
allgemeine Reparaturerfolgsquote und keinen Laufzeitgewinn.

Migration: neuen Ergebnisordner anlegen, keine Checkpoints aus Version 1.0.0 oder
dem Compact-Pilot übernehmen. Der alte Softwarestand bleibt unverändert.

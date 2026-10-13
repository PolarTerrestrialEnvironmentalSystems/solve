# JSON vorher und nachher: tatsächliche Aussage aus der Test-PDF

Die Aussage stammt aus PDF-Seite 2. Zur isolierten Darstellung des Formateffekts
wird **derselbe Referenz-Datensatz** einmal mit und einmal ohne optionale Nullfelder
gezeigt. Das Nachher-Beispiel ist lokal abgeleitet, keine zweite unabhängige Modellantwort.
Die Gleichheit nach Schema-Validierung wurde geprüft. Die Ausschnitte lassen andere
Felder einschließlich der Belege aus; vollständige Datensätze stehen in den JSON-Dateien.

## Vorher (Ausschnitt)

```json
{
  "event_id": "e3",
  "statement": "Wasserentnahme zur Energiegewinnung wurde gegebenenfalls eingeschränkt.",
  "participants": null,
  "measurement_context": null,
  "time_original": null,
  "time_status": "not_stated",
  "assertion_mode": "reported",
  "polarity": "affirmed",
  "source_document": null
}
```

## Nachher (derselbe Ausschnitt)

```json
{
  "event_id": "e3",
  "statement": "Wasserentnahme zur Energiegewinnung wurde gegebenenfalls eingeschränkt.",
  "time_status": "not_stated",
  "assertion_mode": "reported",
  "polarity": "affirmed"
}
```

Die belegte Aussage, ihr Status und die in den vollständigen Dateien enthaltenen
Zitate bleiben gleich. Beim Einlesen ergänzt das Schema definierte Defaults;
die Excel-Spalten ändern sich nicht. Das lokale Beispiel erklärt nur den Vertrag:
Laufzeit spart erst eine bereits vom Modell erzeugte kompakte Antwort.

- `example_verbose.json`: vollständiger normalisierter Referenz-Datensatz.
- `example_compact.json`: derselbe Datensatz ohne optionale Nullfelder.
- `actual_compact_qwen_event.json`: tatsächlich erzeugter kompakter Qwen-Datensatz
  zur gleichen Aussage, unverändert aus der Rohantwort. Andere Detailfelder und die
  lokale ID können bei dieser unabhängigen Extraktion abweichen.

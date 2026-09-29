# SOLVE: PDF-Batch mit feiner Ontologie – Version 1.2.0

Diese Version erhält ganze Absätze, verwendet 4.000 Zeichen als weiche Zielgröße
und ergänzt Beleg-/Vergleichsprüfungen. Die 28 Event- und 30 Beziehungsspalten bleiben
erhalten; das verschachtelte Quantity-Schema wird um optionale wertbezogene
Zeitangaben erweitert (Schema-Version 1.1.0). Frühere Versionen bleiben unverändert.
Fachliche Prüfung der Ergebnisse bleibt erforderlich.

## Neu in 1.2.0

- **4.000 Zeichen sind keine harte Grenze.** Sobald die Zielgröße erreicht wird,
  wird der gerade übernommene Absatz bzw. die Tabellenzeile vollständig beendet.
  Beispiel: 3.500 Zeichen + nächster Absatz mit 2.000 Zeichen ergeben einen Abschnitt
  mit 5.500 Zeichen. Der nächste Absatz beginnt einen neuen Abschnitt.
- Die PDF-Aufbereitung zerlegt Text nicht mehr künstlich nach 1.600 Zeichen oder
  der halben Abschnittsgröße. Absätze werden anhand des Layouts erkannt; Seitenumbrüche
  behalten ihre eigenen Beleg-IDs. PDF-Layout kann echte Absatzgrenzen nicht immer
  eindeutig erkennen. Tabellenzeilen bleiben vollständig.
- Bei technischen Kontext-/Antwortgrenzen kann weiterhin zwischen Blöcken geteilt
  werden, aber niemals innerhalb eines einzelnen Absatzblocks. Ein einzelner zu großer
  Block wird mit Fehlerstatus ausgewiesen, statt still abgeschnitten zu werden.
- Jeder `values`-Eintrag kann `time_original` und `time_normalized` enthalten, etwa
  20,0 m³/s → 1924–1953 und 38,8 m³/s → 1955–1993. Keine neuen Excel-Hauptspalten.
- Themen-, Klassen-, Teilnehmerklassen- und Beziehungstyp-IDs sind im API-Schema
  eingeschränkt. Die lokale Ontologieprüfung bleibt auch bei API-Fallback erhalten.
- Der Prompt verlangt separate wörtliche Belege für jede beteiligte Quellpassage.
- Höchstens eine zusätzliche Qualitätsreparatur je gültiger Abschnittsantwort:
  ausgelöst durch ungültige Belege/IDs oder erkennbare numerische `von … auf …`-
  Vergleiche mit fehlenden Werten/Ausgangszeiträumen. Bestehende Fachinhalte bleiben
  unverändert; nur ungültige IDs/Belege dürfen korrigiert und fehlende Vergleiche durch
  zusätzliche belegte Events ergänzt werden. Das kann ergänzende Dubletten erzeugen.
  Kein vollständiger semantischer Vollständigkeitsnachweis; andere Auslassungen können
  unerkannt bleiben. Fehlende Angaben werden nicht aus Weltwissen ergänzt.
- Abgewiesene oder übersprungene Reparaturen erhalten die Originalantwort.
  `review_items.jsonl` protokolliert `source_quality_repair` mit Gründen und Rohantwortpfaden.
  Anschließend greift der bestehende Selbstbeziehungsschutz. Beide Reparaturarten
  können je einen zusätzlichen Modellaufruf verursachen; es gibt keine Reparaturschleife.

## Neu: kausale Selbstbeziehungen verhindern

- Der Prompt verlangt unterschiedliche belegte Ursache-/Wirkungs-Events und
  untersagt bloße Kopien mit neuen IDs.
- Für `causes`, `contributes_to`, `affects`, `increases`, `decreases`, `prevents`
  werden Selbstbeziehungen aus dem regulären Export ausgeschlossen. Dies wird
  vor und nach Referenzauflösung und Dublettenbereinigung geprüft.
- Nach einer schema-gültigen Abschnittsantwort wird bei erkannten Selbstbeziehungen
  höchstens ein zusätzlicher semantischer Reparaturaufruf angefordert. Der vorhandene
  HTTP-Retry-Mechanismus und gemeinsame Limiter gelten auch dafür. Ein Abschnitt
  ohne betroffene Beziehung löst keinen Reparaturaufruf aus.
- Die Reparatur darf nur betroffene Beziehungen ändern oder zurückziehen und
  nötige belegte Events ergänzen. Vorhandene Events, andere Beziehungen und
  Dokumentmetadaten dürfen sich nicht ändern. Beziehungstyp, Verneinung und
  Unsicherheitsstatus bleiben erhalten. Kopierte Events, erfundene Zitate,
  unbekannte IDs und nach wie vor bestehende Selbstbeziehungen werden abgewiesen.
- Bei Fehler, abgeschnittener Reparaturantwort oder zu großem Kontext bleibt die
  ursprüngliche Extraktion erhalten. Keine rekursiven semantischen Reparaturen.
  Die Exportprüfung sortiert die verbleibende Selbstbeziehung aus.
- Alte und neue Rohantworten sowie Reparaturstatus bleiben nachvollziehbar.
  `review_items.jsonl` enthält `causal_self_relationship_repair` und für verworfene
  Kanten den Grund `causal_self_relationship`. Auch akzeptierte Rücknahmen bleiben
  dokumentiert. `summary.json` zählt Zurückweisungen und Reparaturstatus.

Die endgültige Exportprüfung schützt auch bei Zusammenführung identischer Events
aus verschiedenen Abschnitten. Solche erst abschnittsübergreifend erkannten Fälle
werden ausgesondert, ohne nachträgliche API-Reparatur. Nichtkausale Relationstypen
behalten ihre bisherigen Regeln. Die Prüfung kann semantisch nur ähnlich formulierte
Events nicht sicher als gleich erkennen; eine zulässige Reparatur ist keine
fachliche Freigabe. Unbelegte Datumspräzisierungen und andere semantische Fehler
können trotz der zusätzlichen Prüfungen fortbestehen.

**Für Version 1.2.0 einen neuen Ergebnisordner verwenden.** Checkpoints älterer
Versionen dürfen wegen geänderter Code- und Prompt-Fingerprints nicht übernommen
werden. Die automatische Wiederaufnahme innerhalb derselben Version bleibt möglich.

## Bedienung der zugrunde liegenden Batch-Version

Dieses eigenständige Paket verarbeitet mehrere PDFs ausschließlich mit `ontologie_fein.json`. Es enthält keine grobe Ontologie, keine API-Schlüssel und keine früheren Ergebnisse. Der bisherige Parser im SOLVE-Projekt wird nicht verändert.

## 1. Einrichten

ZIP entpacken und PowerShell im entpackten Ordner `SOLVE_Parsing_Fein_Batch_1.2.0` öffnen. Referenzumgebung: Windows, CPython **3.14.7**. Der Code benötigt mindestens Python 3.11; andere Python-/Betriebssystemversionen wurden nicht geprüft. Für möglichst gleiche Bedingungen die Referenzumgebung verwenden.

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
Copy-Item .env.example .env
notepad .env
```

In `.env` den eigenen API-Key eintragen. Die Kopierzeile nur bei der ersten Einrichtung verwenden. Das Paket enthält feste Versionen aller 16 Laufzeitabhängigkeiten einschließlich transitiver Pakete. Die Installation benötigt Zugang zu einem Python-Paketindex; Wheels sind nicht im ZIP enthalten.

Die Beispielkonfiguration nennt die konkrete Modell-ID des bisherigen Tests. Verfügbare Modelle anzeigen:

```powershell
.\.venv\Scripts\python.exe run_parsing_tests.py --list-models
```

Falls nötig `BLABLADOR_MODEL` vor einem neuen Batch auf eine tatsächlich verfügbare ID setzen. Der Server muss das konfigurierte Ausgabeformat und bei `--thinking-mode off` den Thinking-Schalter unterstützen. Betriebssystem-Umgebungsvariablen haben Vorrang vor `.env`. Niemals `.env` weitergeben.

## 2. PDFs ablegen und lokal prüfen

Alle gewünschten PDFs in `input` kopieren. Der Ordner ist im Paket absichtlich leer. Mit `--recursive` werden auch Unterordner berücksichtigt.

```powershell
.\.venv\Scripts\python.exe parse_directory.py --input input --output results/vorpruefung --dry-run
```

Das bereitet Text und Tabellen ohne API-Aufrufe auf. Für den echten Lauf einen anderen Ausgabeordner verwenden.

## 3. Alle PDFs parsen

```powershell
.\.venv\Scripts\python.exe parse_directory.py --input input --output results/mein_lauf
```

Standardwerte: **nur feine Ontologie**, vier parallele Hauptabschnitte, gemeinsam maximal **30 HTTP-Anfragen pro Minute**, 16.000 Ausgabe-Tokens, Thinking aus, HTTP-Inaktivitäts-Timeout 600 Sekunden und maximal zehn zusätzliche Kontextabfragen pro PDF. Die PDFs laufen in sortierter Dateireihenfolge nacheinander; innerhalb einer PDF arbeiten bis zu vier Threads. Auch Wiederholungen und Modellabfragen zählen zum gemeinsamen Limit. Der persistierte Limiter gilt über Dokument- und Batchgrenzen innerhalb dieser Paketkopie. Andere Programme oder Paketkopien mit demselben Key sind nicht in diesem Zähler enthalten.

Explizit dieselben Optionen setzen:

```powershell
.\.venv\Scripts\python.exe parse_directory.py --input input --output results/mein_lauf --workers 4 --rpm 30 --timeout 600 --max-output-tokens 16000 --thinking-mode off
```

Weitere Optionen: `--recursive`, `--model "MODELL-ID"`, `--chunk-chars 4000` (weiche Zielgröße), `--max-context-calls 10` und `--env-file .env`. Relative Pfade beziehen sich auf den Paketordner, unabhängig vom aktuellen Arbeitsverzeichnis. Absolute Pfade sind ebenfalls möglich. Der Ausgabeordner muss außerhalb des Eingabeordners liegen. Die PDF-Dateinamen dürfen Leerzeichen und Umlaute enthalten.

## 4. Unterbrochenen Lauf fortsetzen

```powershell
.\.venv\Scripts\python.exe parse_directory.py --input input --output results/mein_lauf --resume
```

Alle ursprünglich verwendeten zusätzlichen Optionen wieder angeben. Vollständig abgeschlossene PDFs werden übersprungen; bei unterbrochenen PDFs werden erfolgreiche Checkpoints übernommen. Fehlgeschlagene Abschnitte werden erneut versucht. Der PDF-Bestand darf dabei weder erweitert noch entfernt oder verändert werden. Auch Code, Ontologie, Vorlagen, Einstellungen, Python- und Paketversionen müssen gleich bleiben. Bei Änderungen einen neuen Ausgabeordner wählen. Ergebnisse alter SOLVE-Versionen sind nicht in dieses neue Paket migrierbar. Einen laufenden oder fortzusetzenden Ergebnisordner nicht verschieben.

Bei einem fatalen Fehler stoppt der Batch; folgende Dokumente bleiben als `pending` sichtbar. Exit-Code 0: abgeschlossen oder Dry-run; 2: abgeschlossen mit fehlgeschlagenen Teilabschnitten; 1: Fehler; 130: Tastaturabbruch. Ein harter Prozessabbruch kann `results/.parser.lock` hinterlassen. Diese Datei erst entfernen, wenn der darin genannte Prozess sicher beendet ist. Keine zwei Parser aus derselben Paketkopie gleichzeitig starten. PowerShell geöffnet lassen und Ruhezustand vermeiden.

## Ergebnisse

```text
results/mein_lauf/
  batch_manifest.json
  logs/api_requests.jsonl
  logs/api_requests_per_minute.csv
  combined/events.jsonl
  combined/relationships.jsonl
  <PDF-Name>_<eindeutiger-Schluessel>/
    manifest.json
    coverage.json
    prepared/
    logs/
    fein/
      events.xlsx
      relationships.xlsx
      events.jsonl
      relationships.jsonl
      review_items.jsonl
      summary.json
      checkpoints/
      raw_responses/
```

Jede PDF hat eigene Events und Beziehungen mit Herkunft und stabilen IDs innerhalb des Laufs. Die IDs enthalten den Dokumentlauf als Namensraum; gleichlautende Aussagen aus verschiedenen PDFs kollidieren deshalb nicht. Gleichnamige PDFs aus verschiedenen Unterordnern bleiben getrennt. Auch identische Inhalte an unterschiedlichen Eingabepfaden werden als getrennte Eingaben verarbeitet; es gibt keine automatische dokumentübergreifende Dublettenbereinigung.

Die gemeinsamen JSONL-Dateien enthalten die exportierten Datensätze der bereits fertig bearbeiteten PDFs. Bei Abbruch oder `complete_with_failures` sind sie vorläufig. Die XLSX-Dateien haben die vorgegebenen 28 Event- und 30 Beziehungsspalten. Verschachtelte Daten sind in JSONL vollständig verfügbar. Zwischen den PDFs erfolgt keine zusätzliche LLM-Abfrage oder automatische Beziehungserkennung.

`batch_manifest.json` zeigt den Gesamtstatus und den Status jeder PDF. Während einer PDF zeigen deren eigenes Manifest und API-Logs den Fortschritt. Die gemeinsame Minutenstatistik und die gemeinsamen JSONL-Dateien werden nach jeder PDF aktualisiert. In der CSV ausschließlich `scope=total` für die Gesamtfrequenz auswerten; `detail` nicht zusätzlich summieren.

## Reproduzierbarkeit und Grenzen

- `requirements.lock.txt` fixiert sämtliche Laufzeitpakete. Der Batch prüft die installierten Versionen vor dem Start.
- `RELEASE_SHA256.json` enthält die Prüfsummen der Paketdateien. Mit `verify_release.py` können sie nach dem Entpacken geprüft werden.
- Das Batchmanifest speichert PDF-Prüfsummen, Konfiguration ohne API-Key, Code-/Ontologie-/Vorlagenprüfsummen sowie Python- und Paketversionen. Die Modellidentität wird über alle PDFs und Wiederaufnahmen geprüft. Ein erkennbarer Modellwechsel stoppt den Lauf.
- Temperatur 0 und eine feste Modell-ID garantieren bei einer externen API keine bitidentischen neuen Antworten. Unsichtbare serverseitige Modelländerungen können nicht vollständig erkannt werden. Rohantworten und Checkpoints dokumentieren den tatsächlichen Lauf; Wiederaufnahme verwendet diese Ergebnisse weiter.
- Die Version automatisiert die Standardpipeline. Die speziell auf den früheren Bode-Test zugeschnittene manuelle Nachbearbeitung und historischen Checkpoints sind nicht enthalten. Zu lange Antworten werden bis zu drei Ebenen geteilt; verbleibende Fehler stehen explizit im Ergebnisstatus und in Prüflisten.
- Es findet keine OCR oder Interpretation von Abbildungen/Karten statt. `coverage.json` weist entsprechende Grenzen aus. Technische Vollständigkeit belegt keine fachlich vollständige Extraktion. Zitate, Ontologieklassen, Event-Referenzen und Schema werden geprüft; die Interpretation und feldbezogene Beleglage benötigen weiter fachliche Kontrolle. Unsichere/ungültige Kandidaten bleiben in `review_items.jsonl`.

## Offline-Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Die Tests erzeugen kleine PDFs selbst und simulieren alle HTTP-Antworten. Sie benötigen keinen Schlüssel und testen unter anderem mehrere PDFs, ausschließlich feine Ontologie, getrennte IDs, Wiederaufnahme ohne doppelte API-Aufrufe, Modellwechsel, gemeinsame Frequenzbegrenzung und das Erkennen veränderter Eingaben.

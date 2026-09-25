# SOLVE – hartnäckige, wiederaufnehmbare Webrecherche

Version 1.0.0 · 18. September 2026

Dieses Paket implementiert den angefragten Recherche- und Sammelschritt: Gewässernamen und Zusatzmerkmale einlesen, gemeinsame Suchthemen verfolgen, Quellen auswerten, neue Verweise finden und Ergebnisse mit Herkunft speichern. Es ist ein eigenständig ausführbares Python-Skript. Die anschließende fachliche Faktenextraktion und Übernahme nach PostgreSQL/Neo4j sind ein eigener Projektschritt.

## Schnellstart

1. ZIP entpacken. Im entpackten Ordner eine eigene Python-Umgebung anlegen.
2. `examples/gewaesser.json` kopieren und Gewässer, Stichwörter und Suchthemen bearbeiten.
3. Für GPT die Voreinstellung `search.provider = external`, `review.mode = assistant` verwenden. Für einen selbstständig laufenden Rechner die Blablador-Konfiguration unten verwenden.

Windows / PowerShell, einmalig:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_web_enrichment.py doctor --config examples/gewaesser.json
```

Unter Linux/macOS entsprechend `python3 -m venv .venv` und `.venv/bin/python`. Im Folgenden bezeichnet `python` den Python-Interpreter dieser Umgebung.

```bash
python run_web_enrichment.py plan --config examples/gewaesser.json --workspace web_research_data
python run_web_enrichment.py run --config examples/gewaesser.json --workspace web_research_data
python run_web_enrichment.py status --config examples/gewaesser.json --workspace web_research_data
```

**Im GPT-Modus erzeugt der erste Lauf ohne Startadressen zunächst Suchaufträge.** Die tatsächliche Websuche übernimmt der Assistent; er importiert Treffer und Bewertungen über die dokumentierte Schnittstelle und setzt den Lauf fort. Das Python-Skript hat keinen unmittelbaren Zugriff auf die Suchwerkzeuge eines ChatGPT-Chats. Die genaue Arbeitsanweisung steht in `GPT_WORKFLOW.md`.

Die Datei `run_web_enrichment.py` ist auch einzeln verwendbar; die Konfiguration und für PDF-Text `pypdf` werden weiterhin benötigt. Für reines HTML funktionieren die Kernfunktionen mit der Python-Standardbibliothek.

## Deine Eingabe

```json
{
  "context": "Gewässerentwicklung und Maßnahmen, besonders vor 2000; ältere Berichte über Zitate erschließen.",
  "waterbodies": [
    {
      "id": "B-Mueggelsee",
      "name": "Müggelsee",
      "aliases": ["Großer Müggelsee", "Mueggelsee"],
      "region": "Berlin",
      "keywords": ["Köpenick", "Spree"],
      "identifiers": [],
      "exclude_keywords": [],
      "seed_urls": []
    }
  ],
  "topics": [
    {"name": "Eutrophierung", "keywords": ["Trophie", "Phosphor", "eutrophication"]},
    {"name": "Sanierung", "keywords": ["Restaurierung", "Entschlammung", "restoration"]}
  ],
  "search": {"provider": "external"},
  "review": {"mode": "assistant"}
}
```

| Feld | Bedeutung |
|---|---|
| `id` | Dauerhafte, von dir vergebene Gewässer-ID. Beim Fortsetzen beibehalten. |
| `name`, `aliases` | Name und belegte Namensvarianten. |
| `region`, `keywords` | Lage und weitere Identitätshinweise; keine Pflicht-UND-Verknüpfung aller Wörter. |
| `identifiers` | Bekannte Wasserkörperkennungen als zusätzliche Suchbegriffe. |
| `exclude_keywords` | Hinweise auf mögliche Verwechslungen. Eine Fundstelle wird dann als unklar markiert. |
| `seed_urls` | Optionale verlässliche Startseiten; die Suche funktioniert auch allein über externe Suchtreffer. |
| `topics` | Gemeinsame Themen und Synonyme für alle Gewässer. Synonyme werden auch für neue Suchwege genutzt. |
| `context` | Freie Beschreibung für die KI. Zeitwünsche sind Suchziele, keine harten Publikationsjahresfilter. |

Die Regeln bestätigen keine Gewässeridentität. Ein Name plus Thema ergibt einen Recherchekandidaten. Methodenunterlagen können über den Fundweg indirekt relevant sein. Eine Aussage über eine Messstelle, ein Einzugsgebiet oder einen anderen See wird hier noch nicht zu einem Datenbankfaktum.

## Die vier Ergebnisse

Unter `web_research_data/results/<Gewässer-ID>/` entstehen jeweils CSV und JSONL:

| Datei | Inhalt |
|---|---|
| `01_pdfs` | Heruntergeladene, nicht als fachfremd bewertete PDF-Kandidaten; mit Originalpfad, Prüfsumme, Auslesestatus und Fundwegen. |
| `02_seiteninhalte` | Ausgelesene HTML-/Textquellen und ausdrücklich gekennzeichnete, extern importierte Textauszüge. |
| `03_offene_hinweise` | Gesperrte oder fehlgeschlagene Abrufe, dynamische Seiten, fehlende OCR, nicht unterstützte Formate, Budgetgrenzen und ungeklärte Literaturverweise. Diese Gründe bleiben getrennt. |
| `04_physisches_material` | Hinweise mit ausdrücklichem Quellwortlaut wie „nur im Lesesaal“ oder „nicht digitalisiert“, einschließlich Fundstelle und ggf. Signatur im Originaltext. |

Ein gespeichertes Scan-PDF steht in Kategorie 1 und zusätzlich als offener Auslesefall in Kategorie 3. Die Kategorien sind deshalb nicht disjunkt. Fehlende Online-Treffer und ISBNs werden niemals allein als Beweis für ausschließlich physisches Material gewertet. Hinweise auf physische Bestände bleiben zu prüfende Zuordnungen und können inzwischen veraltet sein.

Originale werden gemeinsam unter `archive/pdf/`, `archive/html/` bzw. `archive/text/` abgelegt. Identische Bytes belegen dort nur einen Dateipfad. `extracted/` enthält Text plus JSON mit Seiten-/Fragmentbezug. Die Manifeste verweisen relativ auf diese Dateien, sodass ein gesamter Dossierordner verschoben werden kann.

Weitere Ausgaben:

- `results/RECHERCHEBERICHT.md`: Bearbeitungsstand, offene Aufgaben und Themen mit vorläufig nützlichen Quellen.
- `results/alle_quellen_inkl_verworfene.jsonl`: auch zurückgestellte und als fachfremd bewertete Kandidaten.
- `suchprotokoll.jsonl`, `fundwege.jsonl`, `abrufprotokoll.jsonl`: nachvollziehbare Such- und Abrufgeschichte.
- `search_requests.jsonl`, `review_requests.jsonl`: offene Arbeitsaufträge für den GPT-Modus.
- `research.sqlite`: vollständiges Arbeitsgedächtnis. **Für Wiederaufnahme diesen gesamten Dossierordner erhalten**, einschließlich Originalen und ausgelesenen Inhalten.

CSV ist UTF-8 mit BOM und Semikolon für Excel. Potenzielle Formelfelder erhalten beim CSV-Export ein führendes Apostroph; JSONL bewahrt die ursprünglichen Texte unverändert.

## Wie die Suche weiterkommt

Das Skript kombiniert Namens-/Kennungssuche, Themen, PDF-Suche, Archive, historische Untersuchungen, Projekte und wissenschaftliche Arbeiten. Erfolglosen Suchanfragen folgen begrenzte neue Varianten mit Synonymen und weniger engen Ortsfiltern. Suchanfragen werden normalisiert und mit Ergebnis, Zeitpunkt und Neuigkeitsgewinn gespeichert. Ein Suchfehler bleibt ein Fehler und wird nicht als Nulltreffer gezählt.

Links werden aus dem vollständigen HTML gelesen, einschließlich PDF-Metadaten, `object`, `iframe`, `embed` und ausgewählten JSON-LD-Downloadangaben. Der Haupttext wird bevorzugt aus `<main>` gewonnen, sofern vorhanden. PDF-Anmerkungslinks, gedruckte URLs und DOIs werden ebenfalls verfolgt. Bibliografische Angaben ohne Link lösen neue Suchaufträge aus; deren automatische Erkennung ist heuristisch und wird im KI-Modus ergänzt.

Der nächste Schritt hängt von Priorität, bereits bearbeiteten Domains, Suchzweigen und Gewässern ab. Regelmäßig bekommt eine wenig erkundete Domain Vorrang. Nützliche Quellen lösen gezielte Recherchen zu noch offenen Themen aus. Methoden und Anlagen können auch ohne erneute Namensnennung weiterführen. Andere benannte Seen in großen Verzeichnissen werden zurückgestuft, aber ihre Links erhalten.

Die Relevanzprüfung erfolgt nach jeder ausgelesenen Quelle. Mit API bewertet zusätzlich das Modell; im GPT-Modus werden prüfbare Bewertungsaufträge exportiert. In beiden Fällen sind positive KI-Bewertungen nur mit wörtlichem Beleg zulässig. Vorgeschlagene Downloadlinks müssen bereits im Quelldokument gefunden worden sein. Neue freie Suchanfragen müssen einen Gewässerbezug enthalten; die Suche nach einer belegten Literaturangabe darf davon abweichen.

Bereits erfolgreich geladene URLs werden beim nächsten Lauf aus dem Archiv verwendet. Unter zwei bisher unbekannten Spiegeladressen muss eine Datei zunächst jeweils abgerufen werden, bevor die Prüfsumme ihre Gleichheit erkennen lässt. Erfolgreiche PDF-Auslesungen werden anschließend gemeinsam genutzt. Abruffehler haben Wartefristen und begrenzte Wiederholungen; dauerhafte Fehler werden erst durch `retry` erneut angefasst.

Das Gedächtnis ist eine protokollierte, regelgestützte Suchsteuerung mit optionalen Modellurteilen. Es ist kein trainiertes Lernmodell und keine Garantie, alles auffindbare Material zu entdecken.

## Eigenes Python mit Blablador

`examples/gewaesser_blablador.json` ist dafür vorbereitet:

- `search.provider = brave`: eine eigenständige Such-API findet URLs.
- `review.mode = api`: ein Modell bewertet geladene Quellenausschnitte und schlägt nächste Schritte vor.

**Blablador dient hier als Textmodell. Für offene Websuche braucht das Skript zusätzlich Suchtreffer**, z. B. von Brave oder importiert aus GPT. Mit `search.provider = none` verfolgt es ausschließlich Startquellen und deren Links. API-Schlüssel sind getrennt.

PowerShell, Werte aus dem jeweiligen Konto einsetzen:

```powershell
$env:BRAVE_API_KEY = "DEIN-SUCH-API-SCHLUESSEL"
$env:BLABLADOR_API_KEY = "DEIN-BLABLADOR-SCHLUESSEL"
$env:BLABLADOR_BASE_URL = "DEINE-AKTUELLE-API-BASISADRESSE-MIT-V1"

python run_web_enrichment.py models --config examples/gewaesser_blablador.json

$env:BLABLADOR_MODEL = "EXAKTE-MODELL-ID-AUS-DER-MODELLLISTE"
python run_web_enrichment.py run --config examples/gewaesser_blablador.json --workspace web_research_data
```

Unter Linux/macOS werden dieselben Variablen mit `export` gesetzt. Die Basisadresse soll ohne `/chat/completions` enden: Das Skript ergänzt `/models` bzw. `/chat/completions`. Es verwendet das verbreitete Chat-Completions-JSON-Format, benötigt keine speziellen Modell-Tool-Aufrufe und erzwingt keine anbieterspezifischen JSON-Modi. Auch kompatible lokale Dienste lassen sich über andere Umgebungsvariablennamen anschließen.

Die aktuelle Blablador-Basisadresse und verfügbare Modelle sind bewusst nicht fest eingetragen. Den authentifizierten Dienst konnte ich ohne deinen Schlüssel nicht prüfen. Anfrageformat, Antwortprüfung, Modellabfrage, Fehlerfall und Wiederverwendung wurden an einer lokalen API-Testinstanz geprüft. Bei einem fehlerhaften Modellaufruf läuft die Regelauswertung weiter; `needs_review` und das Ereignisprotokoll zeigen das sichtbar. Nach Korrektur der Zugangskonfiguration lassen sich solche Quellen mit `reassess` erneut bearbeiten.

## Fortsetzen, aktualisieren und begrenzen

```bash
# Fortsetzen: derselbe Befehl und derselbe Dossierordner
python run_web_enrichment.py run --config examples/gewaesser.json --workspace web_research_data

# Gesperrte/fehlgeschlagene Abrufe und Suchfehler bewusst neu versuchen
python run_web_enrichment.py retry --config examples/gewaesser.json --workspace web_research_data

# Quellen ab einem bestimmten Abrufalter zur Aktualisierung vormerken
python run_web_enrichment.py refresh --days 30 --config examples/gewaesser.json --workspace web_research_data

# Nach geänderter KI-Konfiguration vorhandene Inhalte erneut bewerten
python run_web_enrichment.py reassess --config examples/gewaesser.json --workspace web_research_data

# Nach erhöhten Tiefengrenzen passende zurückgestellte Verweise aktivieren
python run_web_enrichment.py reconsider --config examples/gewaesser.json --workspace web_research_data

# Bewusst Suchmaschinenanfragen erneut ausführen lassen
python run_web_enrichment.py refresh-searches --config examples/gewaesser.json --workspace web_research_data

# Arbeitsstand zum Mitnehmen, inklusive Skript und Konfiguration
python run_web_enrichment.py pack --config examples/gewaesser.json --workspace web_research_data --output SOLVE_recherche_checkpoint.zip
```

Die Befehle `retry`, `refresh`, `reassess`, `reconsider` und `refresh-searches` aktualisieren die Arbeitsliste. Danach `run` aufrufen. Unveränderte, bereits erfolgreiche KI-Urteile werden auch bei `reassess` wiederverwendet; ein anderes Modell/eine andere Basisadresse, geänderte Quelltexte oder vorher gescheiterte KI-Aufrufe lösen eine neue Bewertung aus.

`refresh` nutzt vorhandene ETags bzw. Änderungsdaten. Neue Dateiinhalte erhalten neue Prüfsummen; alte Originale bleiben erhalten. Neue Themen oder Gewässermerkmale erzeugen ein neues Rechercheprofil und verwenden vorhandene Dateien weiter. Frühere Profile bleiben in SQLite erhalten; die normalen Ergebnisdateien zeigen das aktuell konfigurierte Profil.

Wichtige Grenzen in `limits`:

| Einstellung | Standard im Skript | Wirkung |
|---|---:|---|
| `max_fetches_per_run` | 50 | Abrufaufgaben je Aufruf; Weiterleitungen/robots.txt sind zusätzliche HTTP-Anfragen. |
| `max_searches_per_run` | 12 | Such-API-Aufrufe oder ausgegebene externe Suchaufträge je Aufruf. |
| `max_depth` / `max_reference_depth` | 5 / 3 | Getrennte Grenzen für normale Verweise und DOI-Verweise. Literaturrecherchen sind durch das Suchbudget begrenzt. |
| `max_queries_per_waterbody` | 80 | Höchstens so viele ausführbare Suchanfragen je Gewässerprofil; weitere bleiben dokumentiert zurückgestellt. |
| `max_candidates_per_waterbody` | 2000 | Höchstens so viele Quellaufgaben je Profil; weitere Fundwege bleiben im Fundprotokoll. |
| `max_urls_per_host_per_run` | 20 | Verhindert, dass ein Verzeichnis einen ganzen Lauf belegt. |
| `max_download_mb` | 30 | Grenze je Quelldatei. |
| `max_pdf_pages` | 300 | Auslesegrenze je PDF; weitere Seiten werden als offen ausgewiesen. |
| `max_seconds_per_run` | 1800 | Zwischen Aufgaben geprüfte Laufzeitgrenze; ein bereits laufender Parser/Abruf hat eine eigene Zeitgrenze. |

Die Beispielkonfiguration für GPT verwendet kleinere Arbeitsportionen. Ein Budgetstopp beendet den aktuellen Lauf; ein erneuter `run` setzt ihn fort. Aufgaben außerhalb der Kandidatengrenze bleiben als Fundwege sichtbar und können gezielt als Startquelle in ein neues Profil übernommen werden. Ein dauerhaft unbeaufsichtigter Scheduler ist nicht Bestandteil dieses Schritts.

## PDFs und OCR

Digitale PDFs werden mit `pypdf` seitenweise ausgelesen. Ein Unterprozess begrenzt die Auslesezeit; auf unterstützten Unix-Systemen gilt zusätzlich eine Speichergrenze. Bei Scan-PDFs bzw. textarmen Seiten wird der fehlende Auslesegrad protokolliert. Auch gemischte PDFs mit digitalem Text und Bildseiten werden als teilweise ausgelesen ausgewiesen.

Optionale OCR benötigt die Programme `pdftoppm` aus Poppler und `tesseract` im `PATH`, zusätzlich die gewünschten Sprachpakete. Danach:

```json
"ocr": {"enabled": true, "language": "deu+eng"}
```

`doctor` zeigt, ob die Programme vorhanden sind; `tesseract --list-langs` zeigt die Sprachpakete. Pro Dokument sind standardmäßig höchstens zwölf OCR-Seiten erlaubt. Deutsche OCR wurde in dieser Umgebung mangels `deu`-Sprachpaket nicht geprüft; der tatsächliche Bild-PDF→OCR→Text-Ablauf wurde mit `eng` getestet.

Tabellen bleiben als Originale und ausgelesener Text erhalten. Dieser Schritt normalisiert noch keine Tabellenwerte, Einheiten oder Messjahre. Komplexe PDF-Tabellen, Formularportale, JavaScript-Seiten und umfangreiche Bibliografien benötigen gegebenenfalls weitere Adapter oder Nachprüfung. Office-Dateien werden archiviert und als noch nicht unterstütztes Ausleseformat geführt. JavaScript wird nicht ausgeführt, und Zugangssperren werden nicht umgangen.

## Einbau in SOLVE

Das Skript kann neben `raw_data_import.py`, `water_kg_2_import.py` und `neo4j_v2_import.py` liegen. Der neue Dossierordner ist unabhängig vom Neuaufbau der bestehenden Fachschicht. `waterbody.id` sollte später über eine geprüfte Zuordnung auf dauerhafte SOLVE-Gewässerreferenzen verweisen. Das hier verwendete SQLite-Schema entspricht funktional einer vorgeschalteten Quell- und Verarbeitungsschicht; seine JSONL-Ausgaben sind die spätere Importgrenze zu `water_web`/`water_evidence`.

Es gibt bewusst keine Verbindung zu den bestehenden SOLVE-Datenbankzugängen in diesem Skript. Die Umsetzung beruht auf den beiden übergebenen Architekturdateien, nicht auf einer Prüfung der bestehenden Importer.

## Tests und Betriebsgrenzen

```bash
python -m unittest discover -s tests -v
```

Die Tests verwenden einen lokalen HTTP-Server, echte PDF-Dateien, SQLite und eine simulierte Modell-API. Für den optionalen OCR-Test wird zusätzlich Pillow benötigt; ohne OCR-Werkzeuge wird nur dieser Test übersprungen. `TESTBERICHT.md` dokumentiert den durchgeführten Lauf und den kleinen Praxistest mit LfU-Quellen. Windows-Befehle sind vorbereitet; ausgeführt wurden die Tests unter Linux mit Python 3.12.

Ein Dossier wird jeweils von einem Prozess beschrieben; eine Dateisperre verhindert gleichzeitige Schreiber. In einer verwalteten Umgebung mit bereits eingerichtetem HTTP(S)-Proxy darf die Namensauflösung über diesen Proxy erfolgen. Wer nur lokal aufgelöste öffentliche Ziele zulassen möchte, setzt `network.trust_environment_proxy_dns` auf `false`. Es werden keine zusätzlichen Proxys eingerichtet. API-Zugangsschlüssel bleiben in Umgebungsvariablen und werden nicht ins Dossier geschrieben.

Die Tests prüfen technische Abläufe, nicht die Vollständigkeit historischer Recherchen oder die wissenschaftliche Qualität aller Relevanzentscheidungen. Diese sollte im SOLVE-Pilot an bekannten Referenzdokumenten geprüft werden.

## Verwendete Schnittstellen und Quellen

- [Brave Web Search API](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started): dokumentierter HTTP-Suchzugang mit eigenem API-Schlüssel und Suchoperatoren.
- [Blablador](https://helmholtz-blablador.fz-juelich.de/): Dienst für den vorgesehenen Modellzugang; aktuelle Kontoeinstellungen und Modellliste verwenden.
- [pypdf: Text extraction](https://pypdf.readthedocs.io/en/stable/user/extract-text.html): digitaler PDF-Text; OCR wird durch die zusätzliche Werkzeugkette ausgeführt.
- [Python: robots.txt parser](https://docs.python.org/3/library/urllib.robotparser.html): verwendeter Parser für Zugriffsregeln.
- [LfU Brandenburg: Seensteckbriefe](https://lfu.brandenburg.de/lfu/de/aufgaben/wasser/fliessgewaesser-und-seen/gewaesserzustandsbewertung/seensteckbriefe/): öffentliche Quelle des begrenzten Praxistests.

Projektgrundlagen: `SOLVE_PROJEKTUEBERGABE(1).md` und `SOLVE_WEB_ENRICHMENT_KONZEPT(1).md`, in dieser Sitzung bereitgestellt.

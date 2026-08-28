# Interface für den SOLVE Knowledge Graph

Die lokale Weboberfläche beantwortet deutschsprachige Fragen über den von
`db_to_kg.ipynb` aufgebauten Neo4j-Graphen. Jede Antwort enthält neben einer
Kurzinterpretation auch die zugrunde liegende Tabelle. Diese kann direkt als
CSV-Datamart exportiert und beispielsweise in Python, R, Excel oder Power BI
weiterverwendet werden.

## Start

1. PostgreSQL ist für die Abfrageoberfläche nicht erforderlich. Neo4j Desktop
   muss jedoch laufen und `db_to_kg.ipynb` muss zuvor vollständig ausgeführt
   worden sein.
2. In PowerShell im Projektordner starten:

   ```powershell
   .\.venv\Scripts\python.exe .\scripts\run_kg_interface.py
   ```

3. Die Anwendung öffnet standardmäßig `http://127.0.0.1:8765`.

Das Notebook und die Anwendung verwenden dieselben Umgebungsvariablen:

```powershell
$env:NEO4J_URI = "bolt://localhost:7687"
$env:NEO4J_USER = "neo4j"
$env:NEO4J_PASSWORD = "postgres"
$env:NEO4J_DATABASE = "neo4j"
```

Optional können `KG_INTERFACE_HOST` und `KG_INTERFACE_PORT` gesetzt werden.
Ohne Browserstart kann die App mit `--no-browser` ausgeführt werden.

## Unterstützte Analysefragen

Die natürliche Sprache wird bewusst auf kuratierte, lesende Cypher-Abfragen
abgebildet. Dadurch sind Ergebnisse reproduzierbar und fachlich prüfbar. Der
aktuelle Katalog unterstützt unter anderem:

- größten beziehungsweise tiefsten See;
- Zusammenhang zwischen chemischen Überschreitungen und Bewertungen;
- chemische Statusangaben und betroffene Schadstoffe;
- Wasserkörperbewertungen;
- aggregierten Messdaten-Datamart je See und Parameter;
- Beziehungen zwischen Seen und Schutzgebieten.

Die Analyse „Chemie und Bewertung“ nutzt sowohl direkte chemische Statuskanten
als auch den Graphpfad `See → OVERLAPS → Grundwasserkörper → chemischer Status`.
Die Herkunft jeder Ableitung wird im Datamart ausgewiesen. Chemische
Überschreitungen werden zuerst je See aggregiert; dadurch vervielfachen mehrere
Schadstoffe nicht die Bewertungen. Nur
Bewertungsklassen von 1 bis 5 fließen in den Gruppenvergleich ein; der in den
Quelldaten verwendete Sonderwert 999 wird ausgeschlossen. Die Ausgabe beschreibt
eine Assoziation und darf nicht als kausaler Nachweis interpretiert werden.

Hinweis zur Fläche: `new_imports.ipynb` übernimmt `FLAECHE_ATKIS` derzeit in die
Property `area_km2`, obwohl die Quelldatei Hektarwerte enthält. Die Abfrageschicht
normalisiert diese Property deshalb durch Division durch 100 auf km². Diese
Umrechnung muss entfernt werden, sobald der Import selbst die Einheit korrigiert.

Für graphnahe Sonderfragen gibt es einen Expertenmodus. Er akzeptiert eine
einzelne lesende Cypher-Abfrage und öffnet die Neo4j-Session zusätzlich explizit
im Lesemodus. Schreibklauseln werden vor der Ausführung abgelehnt.

## Struktur und Erweiterung

- `kg_interface/queries.py`: fachlicher Abfragekatalog und Sprachrouting;
- `kg_interface/service.py`: Neo4j-Zugriff, Ableitungen und CSV-Aufbereitung;
- `kg_interface/server.py`: lokale HTTP-API;
- `kg_interface/static/index.html`: responsive Benutzeroberfläche;
- `tests/test_kg_interface.py`: Tests für Routing, Interpretation und Leseschutz.

Eine weitere Frage wird als `QueryDefinition` in `QUERIES` ergänzt. Anschließend
wird sie in `classify_question` passenden deutschen Begriffen zugeordnet. Die
Cypher-Abfrage sollte stets einen klar dokumentierten Zeilengrain besitzen und
tabellarische Werte zurückgeben.

`db_to_kg.ipynb` speichert `parameter_name` und `quality_component` nun außerdem
direkt auf `WaterBodyAssessment`. Das ist ein fachlicher Fallback, falls ein
Parameter wegen einer mehrdeutigen Kombination aus Name und Qualitätskomponente
nicht über `FOR_PARAMETER` verbunden werden kann. Damit diese Properties in einem
bereits aufgebauten Graphen erscheinen, das Notebook ab der Modelldefinition und
dem Import der verbleibenden Knoten erneut ausführen.

Tests ausführen:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s scripts\tests -v
```

# Recherche und Validierung am 10.11.2026

Das Paket enthält **2.554 eindeutige WRRL-Wasserkörper** im deutschen Elbegebiet und im räumlichen Schnitt mit BB, BE, ST, SN, MV und TH: **343 Seen-Wasserkörper und 2.211 Fließgewässerabschnitte**. Das vollständige Register und seine Herkunft stehen unter [registry/](registry/); lesbare Landeslisten unter [catalog/](catalog/README.md).

## Tatsächlich erreichte Startquellen

Das finale [Manifest](seeds.json) enthält **10.097 Gewässer-URL-Zuordnungen zu 2.774 verschiedenen finalen URLs**. Derselbe Sammelbericht kann mehreren Wasserkörpern zugeordnet sein. Das ist keine Zahl heruntergeladener PDFs oder unabhängiger Studien.

- Seen: 1.729 Zuordnungen; alle 343 haben Quellen. **Nur sieben erreichen mindestens zehn**, die übrigen 336 haben zwei bis neun angenommene Start-URLs.
- Fließgewässer: 8.368 Zuordnungen; alle 2.211 haben Quellen.
- 9.978 Zuordnungen stammen aus amtlichen Katalogen und konkreten Kennungs-/Seitenbelegen. Nach Zusammenführung und URL-Deduplizierung kommen **119 zusätzliche LLM-bestätigte Zuordnungen** hinzu.
- Höchstens 20 verschiedene URLs pro Gewässer sind erlaubt; im vorliegenden Stand beträgt das Maximum 15.

| See | Angenommene URLs |
|---|---:|
| Großer Stechlinsee | 15 |
| Scharmützelsee | 14 |
| Sacrower See | 14 |
| Großer Müggelsee | 13 |
| Arendsee | 13 |
| Talsperre Bautzen | 12 |
| Senftenberger See | 10 |

Die gewünschte Mindestzahl wurde damit **nicht für alle Seen erreicht**. Die Recherche ist begrenzt; ein Defizit bedeutet nicht, dass keine weiteren Quellen existieren. Touristische Seiten, bloße Aggregatoren, unklare Namensgleichheiten und technisch blockierte Treffer füllen keine fehlenden Plätze auf. [Vollständige Abdeckung](SEED_COVERAGE.md) · [anklickbare Links je See](startlinks/README.md).

## Verfahren und Grenzen

1. **Amtliche Auswahl:** EEA/Wasserrahmenrichtlinie, Filter `countryCode='DE' AND euRBDCode='DE5000'`, danach räumlicher Schnitt mit BKG-Landesgrenzen. Paginierung, Anzahlen und Kennungsmengen der Attribute/Geometrien wurden abgeglichen. Grenzgewässer erscheinen mehrfach in Landeslisten, aber nur einmal im Lauf. Kleine geometrische Überschneidungen bleiben zur Prüfung markiert.
2. **Amtliche Quellen:** tatsächliche Kataloglinks aus Brandenburg, Sachsen und Mecklenburg-Vorpommern; Thüringer Bewertungen; FGG-Elbe-Zustandsberichte 2021/2015 und Maßnahmenprogramme 2021/2015. Identitäten wurden über amtliche Kennungen gebunden, mit ausdrücklich dokumentierter Namespace-Normalisierung bei historischen Codes. HTTP-Zugriff/MIME beziehungsweise konkrete Inhalte wurden geprüft. Das ist keine pauschale Bestätigung jeder Zahl im Dokument.
3. **Breite Webrecherche:** bis zu sechs Such-HTTP-Versuche je See, Wiederverwendung vorhandener Funde und gespeicherter Zwischenstände. Insgesamt **2.048 Serper-HTTP-Aufrufe**. Zusatzprüfung und LLM-Nachprüfung lösten keine erneuten Suchaufträge aus.
4. **Wissenschaftliche Metadaten:** Titelabfragen über DataCite und OpenAlex, zusätzliche lokale Namens-/Themenprüfung und DOI-/URL-Deduplizierung. 283 Kandidaten für 31 Seen; 235 unterschiedliche Ziel-URLs wurden zusätzlich geprüft, davon 154 abrufbar und 81 technisch offen/blockiert/leer. Abrufbar bedeutet noch nicht angenommen. DataCite: 332 erfolgreiche eindeutige Abfragen für die 343 Seen. OpenAlex: 95 erfolgreiche Abfragen, zwei HTTP429 und anschließend 235 ausdrücklich ausgelassene Jobs. Keine Umgehung der Provider-Pause. Titelabfragen finden keine Studien, die einen See ausschließlich im Volltext erwähnen.
5. **Inhaltsprüfung:** Nähe von Gewässername/Kennung und Fachthema, Herkunft, Tourismus-/Aggregator-Filter und strengere Zuordnung bei Namensgleichheit. Konkrete Maßnahmen-, Genehmigungs-, Rekultivierungs- und Monitoringnachrichten sind grundsätzlich geeignet; nicht nur wissenschaftliche Aufsätze.
6. **Blablador-Nachprüfung:** begrenzte Batches mit `alias-fast`, Quellenauszügen und validierten Einzelentscheidungen. 170 erfolgreiche Batches, keine abschließenden API-/Schemafehler. Der ergänzende Collector liefert 400 angenommene Quellen: 245 mit echter LLM-Inhaltsentscheidung und 155 direkte amtliche Steckbriefe mit Kennungsbeleg. Drei fachliche/identitätsbezogene Vetos verbleiben. Viele dieser 400 sind bereits im amtlichen Katalog enthalten; sie werden im finalen Manifest nicht doppelt gezählt. Amtliche Ausnahmen sind ausdrücklich **keine ausgeführte LLM-Prüfung**.

Die ursprünglichen Blablador-Schlüssel antworteten mit HTTP401; für die Endprüfung wurde ein erneuerter Schlüssel verwendet. Fehlgeschlagene Versuche werden nicht als erfolgreiche Bewertungen gezählt. Zugangsdaten, Rohantworten, Auszüge und persönliche Laufdaten werden nicht mit diesem Paket veröffentlicht.

Bei großen Sammelberichten bleiben **Kennungsfundstellen und Kontextseiten getrennt**. Beispielsweise enthält Seite 3 des Maßnahmenanhangs 2015 die Statuslegende. Ein Programmeintrag belegt eine Planung beziehungsweise den dokumentierten Programmstatus und nicht automatisch die Durchführung einer Maßnahme. Berichtszyklen, Messzeiträume und Veröffentlichungsjahre werden nicht gleichgesetzt.

## Softwareprüfung

- 257 Laufzeit-/Verhaltenstests und fünf räumliche Inventartests bestanden. Die räumlichen Tests wurden in einer Umgebung mit den zusätzlichen Geo-Abhängigkeiten ausgeführt.
- Nachprüfung für 1.8.1: 265 Laufzeit-/Verhaltenstests bestanden, einschließlich Suchabschaltung, Wiederaufnahme gespeicherter Suchaufträge und Worker-Weitergabe ohne Serper-Schlüssel. Eine zusätzliche Vorbereitung mit dem echten Manifest ergab alle 2.554 Gewässer in 30 Gruppen und 10.097 Quellenzuordnungen mit `search.provider=none`, ohne Netzwerkaufrufe.
- Bash-Syntax geprüft; Python-3.10-Grammatik geprüft. Die Ausführungstests fanden auf Windows statt. Eine tatsächliche Ausführung auf dem Linux-Server ist damit nicht behauptet.
- Offline-Probe mit dem echten Register: 2.554 Profile in 30 Gruppen, vorbereitete Quellen statt neuer Startsuchaufträge, keine Netzwerkabrufe. Der Statuszähler wurde gegen die Datenbanken geprüft.
- Quellen-Audit: alle Kennungen, URL-Eindeutigkeit/Obergrenze, gesperrte Domains, HTTP-/Identitäts-/Reviewprovenienz und sämtliche Coverage-Summen unabhängig geprüft; keine Fehler.
- 7.328 amtliche Tabellenzuordnungen ergeben eine genaue Seitenauswahl im Parser. Originaldateien bleiben unverändert. Die Teilanalyse wird ausdrücklich ausgewiesen; sie ersetzt keine vollständige Dokumentlektüre.
- Reale lokale Parserproben: ausgewählte Seiten aus dem 106-seitigen Zustandsbericht etwa 1,05 s, aus dem 398-seitigen Maßnahmenbericht etwa 5,27 s und aus dem 780-seitigen historischen Bericht etwa 3,75 s. Das sind Einzelmessungen, keine Laufzeitgarantie für andere Systeme.

SHA-256 von `seeds.json`:

```text
892e22ec8a8c319ef94372bdf6e9962a760802b43f8036b6257663ccdb8a924d
```

Die [Linux-Anleitung](../../ELBE_LINUX.md) beschreibt den strengen Start sowie `--allow-shortfall` für die ausdrücklich gewünschte Einbeziehung von Seen mit weniger als zehn Startquellen. **Ab Crawler 1.8.1** laufen neue Elbe-Kampagnen standardmäßig ohne Such-API: LLM-Vorprüfung, Inhaltsbewertung und Linkverfolgung bleiben aktiv. `run --search-provider none` wählt diesen Modus auch für vorhandene Workspaces; zusätzliche Serper-Suche ist optional. Die oben dokumentierte Serper-Recherche beschreibt die bereits erfolgte Vorbereitung der gelieferten Startquellen.

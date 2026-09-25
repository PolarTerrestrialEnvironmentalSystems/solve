# Arendsee mit Serper und 20 ausgewählten Startlinks

Version 1.6.1, Stand 24.09.2026. Der Standardstarter recherchiert ausschließlich
Arendsee. Die normale Websuche nutzt jetzt Serper statt SearXNG. Blablador bleibt
für die Vorprüfung und Inhaltsbewertung zuständig; Crossref und DataCite bleiben
zusätzliche wissenschaftliche Suchwege.

## Start

Im Projektordner in PowerShell:

```powershell
.\Start-Blablador.cmd
```

Der Starter liest `inputs/sites_blablador.json`. Die beiden Schlüssel werden
bei Bedarf aus `C:/Users/jowals001/awi/solve/scripts/graphrag/data/.env` gelesen:
`SERPER_KEY` und `BLABLADOR_KEY`. Bereits gesetzte Prozessvariablen haben Vorrang.
Andere Variablen aus dieser Datei werden nicht in den Prozess übernommen. Es
wird kein Schlüssel in die Konfiguration, Rechercheergebnisse oder Protokolle
kopiert. Benötigte Pakete: `revised/requirements.txt` (einschließlich python-dotenv).

Der Standardstarter verwendet ab dem nächsten Start das neue Dossier
`dossier_arendsee_serper_v1_6_1`. Dort beginnt ein neuer Rechercheverlauf.
Das bisherige Dossier `dossier_blablador_arendsee_v1_4` bleibt erhalten.
Der gemeinsame `download_cache` steht weiterhin zur Wiederverwendung bereits
geladener Originale zur Verfügung. Ein noch laufender Prozess schreibt bis zu
seiner Beendigung weiterhin in seinen bisherigen Ordner.

## Auswahl der 20 Startlinks

1. Zum Beginn werden `Arendsee` sowie sechs zusätzliche Anfragen mit Messdaten,
   Gewässermaßnahmen, Monitoring, Gewässergüte, Sanierung und Wasserqualität
   geplant. Diese Anfangsabfragen kommen vor der Auswahl; bereits erfolgreich
   gespeicherte Abfragen werden beim Fortsetzen wiederverwendet. Serper werden
   je Anfrage bis zu 20 Ergebnisse angefordert; tatsächlich können es weniger sein.
2. Blablador bewertet URL und Titel in Gruppen von höchstens 20 Kandidaten.
   Es kennt zu diesem Zeitpunkt noch keinen Seiteninhalt. Es prüft Gewässerbezug,
   Quellentyp und erwarteten Nutzen für die Recherchethemen.
3. Aufgenommen werden nur Kandidaten mit `action=accept`, `identity=match`,
   einer passenden Kategorie (`science`, `authority`, `data`, `background`),
   mindestens 60 Wichtigkeitspunkten und mindestens 0,7 Konfidenz.
   Tourismus, Freizeit, Buchung, Werbung, reine Navigation und unklare Kandidaten
   zählen nicht zu den 20. Ein `accept` zusammen mit `category=tourism` wird vom
   Programm ausdrücklich nicht als geeigneter Startlink übernommen.
   Bei Arendsee prüft zusätzlich eine Metadatenregel auf Wasser-/Gewässerbezug
   oder einen bekannten fachlichen Quellenkontext. Generische städtische
   Sensor-/Monitoringportale werden dadurch nicht allein wegen ihres Namens
   als Gewässerquellen akzeptiert.
4. Solange weniger als 20 unterschiedliche URLs ausgewählt sind, folgen weitere
   Ergebnisseiten (bis zu drei je Anfrage), danach gezieltere Anfragen mit
   Monitoring, Gewässergüte, Limnologie, Forschungsbericht, Wasserqualität usw.
   Dafür muss zunächst kein Dokument heruntergeladen worden sein.
5. Nach Erreichen des Ziels endet diese Auswahlphase. Ausgewählte URLs gehen
   durch die vorhandene Einzel-Vorprüfung, Download-/Cacheprüfung und inhaltliche
   Bewertung. Erst danach werden geeignete Links aus den Dokumenten verfolgt.

Die Auswahl bezieht sich auf Websuchtreffer. Auch offene Webtreffer aus alten
SearXNG-/Brave-Läufen müssen die neue Auswahl bestehen. Die schon separat
hinterlegten wissenschaftlichen Einstiege sowie Crossref-/DataCite-Kandidaten
bleiben zusätzliche Recherchewege mit ihren bisherigen Prüfungen; sie zählen
nicht automatisch zu diesem 20er-Ziel. Insgesamt kann ein Lauf daher mehr als
20 Quellen prüfen. Bereits abgeschlossene Dokumentbewertungen werden nicht gelöscht.

Mehrfach gefundene identische URLs zählen nur einmal. Verschiedene Unterseiten
derselben Domain sind unterschiedliche Startlinks. Vorhandene Bewertungen der
Auswahl werden beim Fortsetzen wiederverwendet. Die LLM kann sich bei knappen
Metadaten irren; diese Auswahl ist noch kein wissenschaftlicher Relevanznachweis.

Die zusätzliche Anfangsliste ist in `inputs/sites_blablador.json` editierbar:

```json
"start_links": {
  "enabled": true,
  "target": 20,
  "initial_keywords": [
    "Messdaten", "Gewässermaßnahmen", "Monitoring",
    "Gewässergüte", "Sanierung", "Wasserqualität"
  ],
  "batch_size": 20,
  "min_importance": 60,
  "max_searches": 20,
  "max_pages": 3
}
```

Der konkrete Gewässername wird automatisch vor jeden Begriff gesetzt. Die Liste
enthält deshalb keine Platzhalter wie `See`. Eine Änderung dieser Suchbegriffe
verwirft die bisherigen Startlink-Bewertungen nicht.

## Grenzen und Fortsetzung

Aktiv sind maximal 20 Serper-Aufrufe für die Startlink-Auswahl pro See und Lauf,
zusätzlich begrenzt durch das gemeinsame Suchbudget (80), Vorprüfungsbudget (200)
und Zeitbudget (3600 Sekunden). Eine Gruppenbewertung zählt als ein Vorprüfungsaufruf.
HTTP-429-Fehler halten Serper-Anfragen bis zum Ablauf der Wartefrist an.
Fehlerhafte oder unvollständige LLM-Antworten geben keine ungeprüften Links frei.

Seit 1.6.1 erhalten Modellantworten zunächst eine Prüfung auf leeren Inhalt,
abgeschnittene Ausgabe, gültiges JSON und vollständige Kandidatenentscheidungen.
Fehlerhafte Gruppen werden bis zu zwei Stufen verkleinert (z. B. 20 → 10 → 5)
und erneut bewertet, innerhalb des LLM- und Zeitbudgets. Ein Eintrag wird nur
nach einer vollständig gültigen Gruppenbewertung freigegeben. Bereits erfolgreiche
Teilgruppen bleiben gespeichert. Kandidaten-IDs werden für jede Anfrage lokal
von 1 bis N vergeben und erst nach Prüfung auf die Datenbank-IDs zurückgeführt.

Wenn die Wiederholungen scheitern, bleibt `llm_review_error` als Abschlussgrund.
`ereignisse.jsonl` enthält jetzt einen konkreten `code`, zum Beispiel
`start_links_invalid_json`, `start_links_invalid_id` oder
`start_links_response_token_limit`. Hinzu kommen Gruppenlänge und, soweit
verfügbar, Ausgabe-Abschlussgrund bzw. JSON-Fehlerposition. Rohe Modellantworten
und Schlüssel werden dafür nicht gespeichert. HTTP-/Netzwerkfehler erhalten
ihren eigenen Fehlercode; HTTP-Wartefristen werden nicht durch Sofortwiederholungen umgangen.

Bei fehlenden Treffern, ausgeschöpften Suchvarianten oder Budgets kann die Auswahl
unter 20 bleiben. Dieser Zustand wird ausdrücklich protokolliert. Bereits
ausgewählte Quellen dürfen dann verarbeitet werden; ungeprüfte Web-Startlinks
werden nicht als Ersatz heruntergeladen. Erneutes Starten setzt die Recherche fort.

Das Serper-Konto lehnte im Live-Test die Anfrage `"Arendsee"` mit der Meldung
`Query pattern not allowed for free accounts.` ab. `Arendsee` funktionierte.
Deshalb ist `search.serper_simple_queries=true` aktiv: Anführungszeichen werden
entfernt, `site:ufz.de` wird zu `ufz.de` und `filetype:pdf` zu `pdf`. Diese Begriffe
sind dann normale Suchwörter und keine garantierten Domain-/Dateitypfilter.
Im Protokoll steht der tatsächlich gesendete Suchtext. Für ein Konto, das
erweiterte Suchmuster unterstützt, kann dieser Modus deaktiviert werden.

## Ergebnisse und Prüfprotokolle

Seit 1.6.2 steht pro Gewässer neben `03_offene_hinweise.csv` zusätzlich
`03b_manuelle_pruefung.csv` (und JSONL). Sie enthält Quellen zur manuellen Prüfung:
Zugriffssperren, fehlgeschlagene Abrufe, unlesbare Inhalte und unsichere bzw.
offene Bewertungen bereits geladener Inhalte. Prüfgrund, Fundseiten und Linktexte
stehen in eigenen Spalten. Bloße Warteschlangen-/Budgeteinträge und eindeutig
irrelevante Quellen werden nicht allein deshalb aufgenommen. Eine abgeschlossene
Inhaltsprüfung ersetzt ältere Unsicherheit der Metadatenbewertung.
Die Liste wird bei jedem Export neu erzeugt; sie dokumentiert noch ausstehende,
nicht bereits erfolgte menschliche Prüfungen. Der bisherige Export bleibt bestehen.

Im Ordner `dossier_arendsee_serper_v1_6_1/results/`:

- `startlink_auswahl.json`: Ziel, erreichte Anzahl, Status sowie jede ausgewählte
  oder zurückgestellte URL mit Kategorie, Wichtigkeit, Konfidenz und Begründung.
- `laufprotokoll.jsonl`: Such-/LLM-Aufrufe und Abschlussgrund, zusätzlich der
  Stand der Startlink-Auswahl.
- `RECHERCHEBERICHT.md`: Ergebnisse der anschließenden Inhaltsprüfung.
- `ereignisse.jsonl`: Ereignisse der Auswahl und des weiteren Crawls.

Originaldateien liegen unter `archive/`, die Wiederverwendung erfolgt weiterhin
über `download_cache`. Die technische Prüfung liegt unter
`validation/serper_20260924/`; dort stehen Tests und begrenzte Live-Abfragen.
Das normale Arendsee-Dossier wurde bei diesen Live-Tests nicht verändert.

Serper-Endpunkt: `POST https://google.serper.dev/search`, Schlüssel im Header
`X-API-KEY`. Verarbeitet wird die Liste `organic` (Titel und Link), keine Werbung,
kein Knowledge Graph und keine gespeicherten Suchtextauszüge.
Anbieter: [Serper](https://serper.dev/).

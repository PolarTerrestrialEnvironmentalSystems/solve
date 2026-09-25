# Wissenschaftliche Recherche für alle konfigurierten Seen

**Aktualisierung 1.6.0:** Der Standardstarter bleibt auf Arendsee beschränkt.
Die Websuche der aktuellen Konfigurationen verwendet Serper statt SearXNG.
Arendsee erhält zusätzlich die Auswahl von 20 geeigneten Web-Startlinks.
Wegen der im Live-Test festgestellten Serper-Kontobeschränkung werden Domains
und Dateitypen als einfache Suchbegriffe übergeben; `site:`/`filetype:` sind
dort keine aktiven Filter. Crossref, DataCite und die direkten wissenschaftlichen
Einstiege bleiben erhalten. Details: [SERPER_ARENDSEE.md](SERPER_ARENDSEE.md).
Der folgende Stand 1.5.0 beschreibt die Einführung der wissenschaftlichen Suche.

Stand: 24.09.2026, Programmversion 1.5.0. Die wissenschaftliche Suche wird jetzt
aus dem Namen und den hinterlegten Namensvarianten jedes Gewässers erzeugt.
Zusätzliche manuell ausgewählte Einstiege sind optional. Der Standardstarter
bleibt bei Arendsee; ein eigener Starter recherchiert alle 33 ausgewählten Gewässer.

## Automatische Suchwege

| Weg | Was gesucht wird | Zugang |
|---|---|---|
| Allgemeine Websuche | Gewässername, PDFs, Themen und bevorzugte Behördendomains | SearXNG/Blablador |
| Wissenschaftliche Portale | Gewässername kombiniert mit `site:<domain>` | SearXNG/Blablador |
| Crossref | Bibliografische Nachweise von Veröffentlichungen | Bestehende öffentliche Crossref-API |
| DataCite | Datenpublikationen und Repositorien; Name in Titel oder Beschreibung | Neue öffentliche DataCite-API, ohne zusätzlichen Schlüssel |

Die neun wissenschaftlichen Suchdomains sind `ufz.de`, `igb-berlin.de`,
`pangaea.de`, `copernicus.org`, `link.springer.com`, `link.springernature.com`,
`journals.plos.org`, `onlinelibrary.wiley.com` und `sciencedirect.com`.
Es handelt sich um neun Domains, darunter zwei Adressen von Springer Nature.
Die Domain-Abfragen werden entsprechend Fortschritt, Quellengewinn und Budget
erzeugt. Es werden keine Portal-Startseiten für jeden See vollständig gecrawlt.

Beispielsweise entstehen für Geiseltalsee Suchvarianten wie
`"Geiseltalsee" site:ufz.de` oder `"Geiseltalsee" site:pangaea.de`.
Daneben fragt der Crawler DataCite nach dem Namen in Titel und Beschreibung ab.
Die vorhandene Gewässerprüfung filtert die Metadaten auf passende Namen und
Identitätsregeln. Beschreibungen werden dafür nur vorübergehend gelesen und
nicht als Volltext gespeichert. Gespeichert werden Titel, URL, DOI und Herkunft.
Eine solche Zuordnung ist ein Recherchekandidat, kein bestätigter Fachbefund.

[DataCite beschreibt den öffentlichen Lesezugang ohne Authentifizierung](https://support.datacite.org/docs/api).
[PANGAEA nennt DataCite als Suchmöglichkeit für seine Metadaten](https://wiki.pangaea.de/wiki/Data_Access_and_Reuse).
Die übrigen Portale erhalten keine neue eigene API-Anbindung. Crossref und
DataCite können Kandidaten liefern, während SearXNG-Suchrouten ausgefallen sind.
Für Dokumentabruf und LLM-Bewertung gelten anschließend dieselben Regeln.

## Konfiguration und Start

Die Einstellungen stehen außerhalb der Gewässerprofile:

```json
"scientific_discovery": {
  "enabled": true,
  "datacite_enabled": true,
  "domains": ["ufz.de", "igb-berlin.de", "pangaea.de", "copernicus.org",
              "link.springer.com", "link.springernature.com", "journals.plos.org",
              "onlinelibrary.wiley.com", "sciencedirect.com"],
  "max_name_variants": 3
}
```

Diese Erweiterung gilt für die Strategie `focused`. Die beiden aktuellen
Konfigurationen aktivieren sie. Ältere Konfigurationen ohne diesen Block behalten
ihr bisheriges Verhalten. DataCite kann separat deaktiviert werden.

| Umfang | Starter im Projektordner | Konfiguration | Ergebnisordner |
|---|---|---|---|
| Arendsee | `.\Start-Blablador.cmd` | `inputs/sites_blablador.json` | `dossier_blablador_arendsee_v1_4/results/` |
| Alle 33 | `.\Start-Blablador-Alle-Seen.cmd` | `inputs/sites_blablador_33_scientific.json` | `dossier_blablador_alle_33_v1_5/results/` |

Originaldateien liegen jeweils unter `archive/`. Der gemeinsame `download_cache`
ermöglicht die Wiederverwendung vorhandener Downloads. `BLABLADOR_KEY` bleibt
für die Vor- und Inhaltsprüfung erforderlich.

Beide Konfigurationen erlauben pro Lauf insgesamt höchstens 80 Suchaufrufe,
120 Abrufversuche, 200 LLM-Vorprüfungen, 80 Inhaltsprüfungen und 3600 Sekunden;
die zwölf möglichen SearXNG-Kontrollaufrufe haben ein separates Budget.
Diese Grenzen gelten gemeinsam für alle Seen, nicht je See. Bei 33 Seen werden
zunächst 99 Suchaufgaben geplant: je eine für SearXNG, Crossref und DataCite.
Sie müssen daher nicht alle im ersten Lauf ausgeführt werden. Wiederholtes
Starten mit demselben Starter setzt die gespeicherte Warteschlange fort.

Eine erfolglose DataCite-Abfrage bedeutet nicht, dass keine Forschung zum See
existiert. Es werden maximal 20 Ergebnisse der ersten Metadatenseite verarbeitet;
derzeit gibt es dort keine weitere Seitennavigation. Hinterlegte Namensvarianten
können zusätzlich abgefragt werden. Portalabdeckung, Schreibweisen, Suchbudgets
und die Verfügbarkeit von SearXNG begrenzen die Suche weiterhin.

## Ergänzende direkte Einstiege für Arendsee

Die acht zuvor ausgewählten Quellen bleiben unter `scientific_sources` stehen.
Jeder Eintrag gilt ausschließlich für seine `waterbody_id`; Arendsee-spezifische
Quellen werden nicht als Startseiten für andere Seen übernommen.

| Portal | Einstieg | Zweck |
|---|---|---|
| IGB | [Publikationen zum Arendsee in Fachzeitschriften](https://www.igb-berlin.de/publikationen-zum-arendsee-fachzeitschriften) | Gewässerspezifische Publikationsliste mit Links unter anderem zu Wiley, ScienceDirect, MDPI, PLOS und Copernicus. |
| IGB | [Dossier zur Nährstoffbelastung](https://www.igb-berlin.de/sites/default/files/media-files/download-files/IGB_Dossier_Naehrstoffbelastung_Arendsee.pdf) | Forschungsbericht mit Literaturverzeichnis. |
| UFZ | [Paläolimnologische Untersuchung, 1998](https://www.ufz.de/index.php?en=20939&pub_id=8622) | Publikationsnachweis mit Abstract, DOI und weiterführendem Dokumentlink. |
| UFZ | [Eutrophierungsgeschichte, 1998](https://www.ufz.de/index.php?en=20939&pub_id=8971) | Historischer Publikationsnachweis mit DOI. |
| PANGAEA | [Messdatenserie zum Arendsee](https://doi.pangaea.de/10.1594/PANGAEA.894693) | Datenpublikation mit verknüpften Datensätzen und Literatur. |
| Springer Nature | [Vivianit und Phosphormobilisierung](https://link.springer.com/article/10.1007/s11368-025-03986-z) | Frei zugängliche Fachveröffentlichung mit Arendsee-Untersuchung, PDF und Literatur. |
| Copernicus / HESS | [Temperatur- und Mischungsprognosen europäischer Seen](https://hess.copernicus.org/articles/23/1533/2019/) | Fachveröffentlichung mit Arendsee als einem der untersuchten Seen. |
| PLOS One | [Schwefel-Eisen-Verhältnis und Vivianit](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0143737) | Fachveröffentlichung aus der IGB-Arendsee-Publikationsliste. |

Die Quellenangaben sind dokumentierte Einstiegspunkte; die einzelnen
wissenschaftlichen Aussagen werden dadurch nicht als fachlich geprüft übernommen.

## Abruf und Fortsetzung

Der Standardstarter verwendet den bestehenden Ordner `dossier_blablador_arendsee_v1_4`.
Der Planer ergänzt die neuen URLs einmal je Gewässerprofil. Schon abgeschlossene
Aufgaben bleiben abgeschlossen. Bestehende Einstufungen durch die LLM-Vorprüfung
werden durch einen Neustart nicht automatisch aufgehoben.

Jede neue Quelle durchläuft die bestehende Blablador-Vorprüfung, den normalen
Abruf mit Cache und die Inhaltsprüfung. Anschließend kann der Crawler geeignete
Publikations-, DOI-, PDF- und Literaturverweise weiterverfolgen. Die Suche bleibt
an Gewässerbezug, Laufbudget, Abrufgrenzen und die Zugänglichkeit der Quellen
gebunden; eine einzelne Einstiegsseite ist keine Anweisung zum vollständigen
Herunterladen eines Verlagsportals.

PANGAEA dient hier zunächst als Datenpublikations- und Literaturquelle. Eine
systematische Übernahme aller verlinkten Tabellen- oder Messdateien ist damit
nicht implementiert.

Direkte Einstiege benötigen keinen vorherigen Suchmaschinen-Treffer.

## Wie viele Startwebseiten?

Die allgemeine Websuche startet mit einer Abfrage je Gewässer und übernimmt
bis zu 20 Treffer pro Suchantwort als Kandidaten. Weitere Abfragen und Links
werden im Lauf ergänzt; dies ist keine feste Liste aus 20 Startseiten.
Für Arendsee sind außerdem zwei ältere direkte Einstiege hinterlegt: der
HYDREG-Bericht des LHW und eine UFZ-Publikationsliste. Mit den acht zusätzlichen
wissenschaftlichen Einstiegen sind es zehn feste Start-URLs für Arendsee.
Für den allgemeinen Weg außerhalb der Publikationssuche ist davon der
HYDREG-Bericht der feste Einstieg; LHW und LAU sind bevorzugte Suchdomains.

In der vollständigen Auswahl gibt es 30 bisherige Gewässer-URL-Zuordnungen
zu acht verschiedenen URLs, zusätzlich die acht Arendsee-Einstiege.
Die Anzahl je Gewässer ist unterschiedlich. Manche Seen besitzen keine
manuell hinterlegte Start-URL und beginnen mit den Suchdiensten.

## Weitere Quellen ergänzen

Ein Eintrag enthält:

```json
{
  "waterbody_id": "selected_20d78bdf05456a8f",
  "url": "https://www.igb-berlin.de/publikationen-zum-arendsee-fachzeitschriften",
  "title": "IGB: Publikationen zum Arendsee in Fachzeitschriften",
  "search_domain": "igb-berlin.de"
}
```

`portal`, `kind`, `checked`, `discovery_url` und `note` dokumentieren optional
die Herkunft. `search_domain` ist ebenfalls optional; ohne dieses Feld wird nur
der direkte Einstieg ergänzt. Die Einträge werden außerhalb von `waterbodies`
gespeichert, damit neue Startquellen nicht die Identität des Rechercheprofils
und damit den vorhandenen Fortschritt verändern.

## Validierung

Version 1.5.0: **111 automatisierte Tests bestanden.** Die Planung für alle
33 Gewässer wurde ohne Netzwerkzugriff geprüft. Drei echte DataCite-Abfragen
ergaben einen Kandidaten für Geiseltalsee, 20 für Rappbodetalsperre und keinen
für die erste Abfrage „Großer Goitzschesee“. Bei diesem Test wurden keine
Dokumente heruntergeladen und keine LLM-Aufrufe ausgeführt. Das bestehende
Arendsee-Profil und seine 742 Aufgaben bleiben erhalten.

Details: `validation/general_science_20260924/PRUEFBERICHT.md`.
Sicherung des vorherigen Stands: `validation/before_general_science_20260924/`.
Der frühere Live-Test der IGB-Liste mit Vor- und Inhaltsprüfung liegt weiterhin
unter `validation/scientific_sources_20260924/`. Der neue Test ist kein
vollständiger Recherchelauf für alle Seen und bestätigt keine flächendeckende
Erreichbarkeit der Portale.

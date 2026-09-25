# Aktueller Rechercheinput: nur Arendsee

Quelle: `inputs/sites_blablador.json`. Stand: 24.09.2026.

Auf Wunsch des Nutzers ist vorerst nur Arendsee aktiv. Die vollstaendige Auswahl mit 33 Gewaessern ist unter `inputs/sites_blablador_33_v1_4.json` gesichert.

Alle 19 Recherchethemen bleiben erhalten. Der erweiterte Lauf erlaubt jetzt bis zu 60 Minuten, 80 Suchaufrufe, 120 Abrufaufgaben, 200 LLM-Vorpruefungen und 80 Inhaltspruefungen. Das Erkundungsbudget fuer unklare Quellen betraegt 20. Der Standardstarter schreibt weiterhin nach `dossier_blablador_arendsee_v1_4` und setzt dort fort.

## Gewaesserprofil

- Name: Arendsee
- ID: `selected_20d78bdf05456a8f`
- Alias: Lake Arendsee
- Kontext: Vom Nutzer ausgewähltes Gewässer. Orts- und Gewässeridentität anhand der Quellen prüfen; nicht mit ähnlich benannten Nachbargewässern gleichsetzen. Sachsen-Anhalt dient als regionaler Suchkontext, nicht als bestätigte administrative Zuordnung. Gemeint ist der See. Stadtgeschichte, Kommunalwahlen und Hauptsatzung sind allein kein Gewässerbeleg.
- Bevorzugte Domains: ufz.de, lhw.sachsen-anhalt.de, lau.sachsen-anhalt.de

## Themen und Keywords

| Thema | Keywords |
|---|---|
| Identität und Grunddaten | Gewässerkennung, Gewässersteckbrief, historischer Name |
| Entstehung und Morphometrie | Morphometrie, Seebecken, Tiefe |
| Wasserhaushalt | Wasserstand, Grundwasser, Zuleitung |
| Gewässergüte und Nährstoffe | Phosphor, Trophie, Eutrophierung, water quality |
| Biologische Entwicklung | Makrophyten, Phytoplankton, Fische |
| Sedimente und Schadstoffe | Sediment, Schadstoff, Schwermetall |
| Einzugsgebiet und Landnutzung | Einzugsgebiet, Landnutzung, Entwässerung |
| Abwasser und Industrie | Abwasser, Kläranlage, Industrie |
| Wasserbauliche Eingriffe | Wasserbau, Kanal, Wehr, Ausbaggerung |
| Fischerei und sonstige Nutzung | Fischerei, Badenutzung, Schifffahrt |
| Historische Gesamtentwicklung | Geschichte, historisch, Chronik, 1990 |
| Sanierung und Restaurierung | Sanierung, Restaurierung, Biomanipulation |
| Maßnahmen im Einzugsgebiet | Nährstoffreduktion, Wiedervernässung |
| Umsetzung und Wirkung von Maßnahmen | Erfolgskontrolle, Vorher-Nachher, Maßnahmen |
| Amtliche Bewertung und Planung | WRRL, Bewirtschaftungsplan, Zustand |
| Naturschutz | FFH, Managementplan, Naturschutzgebiet |
| Messreihen und Monitoring | Monitoring, Messreihe, Langzeitdaten |
| Paläolimnologie und Sedimentarchive | Paläolimnologie, Sedimentkern, palaeolimnology |
| Klima und Extremereignisse | Dürre, Hochwasser, Fischsterben |

## Suchablauf

Die Strategie `focused` plant anfangs die SearXNG-Suche `"Arendsee"` und die bibliografische Crossref-Suche `Arendsee`. Weitere Suchvarianten werden nach Quellenpruefung und Themenluecken geplant; es wird nicht jedes Keyword sofort als Anfrage versendet.

Crossref plant auch bei ausgefallenem SearXNG weitere Varianten, beispielsweise zu phosphorus, sediment und groundwater sowie aus vorhandenen Quellenbewertungen. Bei fälligen Suchwiederholungen kann der Lauf innerhalb seines Zeitbudgets warten. Strg+C beendet mit gespeichertem Zwischenstand. Einzelheiten: `RECHERCHE_V1_4.md`.

Die separaten technischen Suchdienstkontrollen verwenden weiterhin feste Testanfragen zu Bergwitzsee und Arendsee/UFZ. Daraus werden keine Bergwitzsee-Rechercheaufgaben erzeugt.

## Startquellen

Zusätzlich zu den beiden ursprünglichen Startquellen sind acht wissenschaftliche
Einstiege aus IGB, UFZ, PANGAEA, Springer Nature, Copernicus/HESS und PLOS One
aktiviert. Der Katalog steht in `WISSENSCHAFTLICHE_QUELLEN.md`, die Einträge im
obersten Konfigurationsfeld `scientific_sources`. Die bestehende Gewässer-ID
und das Rechercheprofil bleiben erhalten.

- [HYDREG 2010: hydrologisches Regime der Oberflächenwasserkörper in Sachsen-Anhalt; Gewässerliste mit Arendsee](https://lhw.sachsen-anhalt.de/fileadmin/Bibliothek/Politik_und_Verwaltung/Landesbetriebe/LHW/neu_PDF/5.0_GLD/Dokumente_GLD/Wasserhaushalt_Bio_Gew-Struktur/Endbericht_HYDREG_2010.pdf)
- [UFZ-Publikationsliste: Phosphorus input by nordic geese to the eutrophic Lake Arendsee, Germany](https://www.ufz.de/index.php?de=40943)

## Gesamtauswahl wieder aktivieren

Die gesicherte JSON-Datei kann wieder als `inputs/sites_blablador.json` verwendet werden. Fuer einen Gesamtlauf im Starter ein eigenes Dossier waehlen (beispielsweise `dossier_blablador_auswahl_33_v1_4`), damit der Arendsee-Lauf getrennt bleibt.

# PDF-Fakten → Ontop-VKG (Anschluss-Paket)

Dieses Paket bringt die aus den GEK-PDFs extrahierten **Befunde** (Ereignisse,
Zustände, Maßnahmen) und **Ursache→Wirkung-Kanten** in das bestehende
**Ontop-VKG** über PostgreSQL. Es ersetzt nichts — es **ergänzt** Mappings, die
über die amtliche Wasserkörper-IRI `resource:water-body/{eu_cd_lw}` an denselben
Knoten andocken wie die DB-Daten (Messungen, Geometrien).

## Inhalt
| Datei | Zweck |
|---|---|
| `pdf_event.csv` | Befunde: `event_id, eu_cd_lw, record_type, statement, time_original, source_document, quote` |
| `pdf_relationship.csv` | Kausalkanten: `source_event_id → target_event_id`, Typ, Mechanismus |
| `pdf_waterbody.csv` | kanonische Gewässer + (soweit aufgelöst) `eu_cd_lw` + Kartenposition |
| `wb_name_to_code.csv` | **Prüf-Tabelle** Name→Code (aus der Übersichtskarte, Status `auto`/`review`) |
| `water-pdf.ttl` | Ontologie-Ergänzung (DocumentedFinding, CausalLink …) |
| `water-mappings-pdf.obda` | Ontop-Mappings für die obigen Tabellen |
| `views.sql` | CREATE TABLE/VIEW + `\copy`-Ladebefehle |

## Einbau in euer `water-kg-ontop/`
1. **Tabellen laden:** `psql -d water_kg -f views.sql`, dann die drei `\copy`-Zeilen ausführen.
2. **Ontologie:** `water-pdf.ttl` neben `water-core.ttl` legen und entweder zusammenführen
   oder per `owl:imports` einbinden.
3. **Mappings:** Inhalt von `water-mappings-pdf.obda` an `water-mappings.obda` anhängen
   (gleiche Prefixes; eine `[MappingDeclaration]`-Collection genügt — Blöcke zusammenführen).
4. Ontop neu starten. Fertig — die PDF-Befunde sind Teil desselben SPARQL-Graphen.

## Was es dann kann (Beispiel-Query)
Alles, was wir über einen Wasserkörper wissen — **DB-Messung UND PDF-Wissen** an einer IRI:

```sparql
PREFIX water: <https://example.org/ontology/water#>
PREFIX sosa:  <http://www.w3.org/ns/sosa/>
SELECT ?finding ?statement ?mechanism WHERE {
  ?wb a water:WaterBody .
  # DB-Seite (aus Ontop): Messungen am selben Wasserkörper
  OPTIONAL { ?m sosa:hasFeatureOfInterest ?wb . }
  # PDF-Seite (dieses Paket): belegte Befunde + kausale Mechanismen
  ?finding water:concernsWaterBody ?wb ; water:statementText ?statement .
  OPTIONAL { ?link water:effect ?finding ; water:mechanism ?mechanism . }
  FILTER(STRENDS(STR(?wb), "SAL17OW06-00"))   # z. B. die Bode
}
```

## Stand der Code-Zuordnung
`eu_cd_lw` wird **amtlich aus den WRRL-Daten** gesetzt (`DRAINBASIN_DEST`, Spalte
`EU_CD_WB`, Name über `DRAIN_NA`; bei mehrdeutigen Namen wird die Bode-Planungseinheit
`SAL17` bevorzugt). Ergebnis: **33 von 86 Gewässern** haben einen Code, das deckt
**~58 % aller Ereignisse** ab (allein die Bode `DERW_DEST_SAL17OW01-00` trägt 617).
Die Spalte `code_source` in `wb_name_to_code.csv`/`pdf_waterbody.csv` sagt, woher der
Code kommt (`drainbasin` = amtlich, `karte` = aus der Übersichtskarte, Fallback).

**Offen:** Die restlichen ~42 % sind generische Nennungen („Gewässer", „Einzugsgebiet …")
oder sehr kleine/namenlose Features. Für mehr Abdeckung: `wb_name_to_code.csv` kurz
durchsehen (die leeren `eu_cd_lw` von Hand ergänzen) oder Namensvarianten/Aliase pflegen.
Die View `v_pdf_event_rdf` ist der Ort, um `eu_cd_lw` später noch per JOIN zu verfeinern.

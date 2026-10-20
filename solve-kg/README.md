# SOLVE – Knowledge-Graph aus Gewässer-Berichten

Pipeline, die aus amtlichen Gewässer-PDFs (GEK-Endberichte, WRRL-Gewässerberichte)
belegte **Befunde** (Zustände, Maßnahmen, Prozesse, Messungen) und **Ursache→Wirkung-Kanten**
extrahiert und zu einem Knowledge-Graphen verbindet. Jeder Befund hängt – soweit auflösbar –
an der amtlichen Wasserkörper-IRI `resource:water-body/{eu_cd_lw}`, damit er im selben
SPARQL-Graphen neben den DB-/Geo-Daten (Ontop-VKG) steht.

## Was dieses Repo neu kann: Cross-Region-Verknüpfung

Bisher verband die Pipeline zwei PDFs nur dort, wo sie **denselben Gewässernamen** nennen.
Ein Bericht aus einer *benachbarten* Region (andere Wasserkörper, aber hydrologisch
verbunden) blieb unverknüpft.

`topology.py` + `region_link.py` schließen diese Lücke über die amtliche
**Gewässerkennzahl (GKZ)**, die die Fließtopologie hierarchisch kodiert:

<img width="846" height="411" alt="Bildschirmfoto 2026-10-20 um 11 01 52" src="https://github.com/user-attachments/assets/5758c3b1-600e-4d91-a618-68984a05e7a2" />


```
5        Elbe            (Flussgebietseinheit)
└ 56     Saale
  └ 568  Bode            (Hauptlauf)
    ├ 5684   Selke       → mündet in die Bode   (drains_into)
    ├ 5686   Holtemme    → mündet in die Bode   (drains_into)
    └ 56832  Rappbode    → mündet in die Bode   (drains_into)
  └ 5676 Wipper          → mündet in die Saale  (shares_catchment mit Bode, via Saale)
4  …     Weser (z. B. Aller 48) → KEINE Verbindung zur Bode
```

Daraus folgen zwei Verknüpfungsarten:

1. **Räumlich** – für jedes Wasserkörper-Paar die hydrologische Beziehung
   (`same_system` / `drains_into` / `receives` / `shares_catchment`). Paare aus
   *verschiedenen* Dokumenten werden als `cross_document` markiert.
2. **Kausal (Kandidaten)** – wo ein Oberlieger (Dok A) einen Unterlieger (Dok B)
   speist, werden Ursache→Wirkung-*Vorschläge* gebildet: ursächliche Befunde oben
   (Maßnahme/Prozess/Ereignis) → Zustands-/Messbefunde unten.

> Die kausalen Cross-Region-Kanten sind **topologisch abgeleitete
> Kandidaten** (`confidence=inferred`) zum Sichten – **keine** aus dem Text belegten
> Aussagen. Die textbelegten Kausalkanten stehen weiterhin nur in `pdf_relationship.csv`.

### Beweis (ohne API, ohne große Daten)

`tests/test_cross_region.py` simuliert ein Nachbar-PDF über die **Selke** (Planungseinheit
SAL20, das Wort „Bode" kommt darin nicht vor) und zeigt, dass es trotzdem räumlich
(`drains_into` Bode) **und** kausal (Selke-Prozess → Bode-Zustand) andockt:

```bash
python tests/test_cross_region.py
#   ok  test_spatial_relations
#   ok  test_neighbor_pdf_attaches
#   ok  test_same_document_not_crosslinked
```

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # nur für die LLM-/PDF-Extraktion nötig

# Cross-Region-Teil läuft ohne Drittpakete:
python tests/test_cross_region.py          # Beweis-Test

# Topologie-Demo gegen echte WRRL-Daten (lokal, nicht im Repo):
python topology.py /pfad/WRRL/current/DRAINBASIN_DEST/DRAINBASIN_DEST.dbf

# Räumliche + kausale Kanten aus bereits exportierten Fakten erzeugen:
python region_link.py \
    --events       vkg_export/pdf_event.csv \
    --waterbodies  vkg_export/pdf_waterbody.csv \
    --drainbasin   /pfad/WRRL/current/DRAINBASIN_DEST/DRAINBASIN_DEST.dbf \
    --out          vkg_export/
# -> spatial_edges.csv, cross_doc_causal_candidates.csv, region_link_summary.json
```

## Pipeline (vom PDF zum Graphen)

| Schritt | Skript | Zweck |
|---|---|---|
| 1. Aufbereitung | `prepare_document.py` | PDF → Blöcke/Seiten (pdfplumber/pypdf) |
| 2. Extraktion | `extract_facts.py`, `parse_directory.py` | LLM-Extraktion von Events/Relationships (Blablador) |
| 3. Validierung | `validate_export.py`, `run_parsing_tests.py` | Schema-Prüfung, XLSX-Export, Tests |
| 4. Merge | `merge_entities.py` | Läufe zu einem Graphen (Namens-Verschmelzung) |
| 5. VKG-Export | `export_vkg.py` | Fakten → CSV + Name→Code (`eu_cd_lw`) via DRAINBASIN/Karte |
| 6. **Cross-Region** | **`topology.py`, `region_link.py`** | **räumliche + kausale Verknüpfung über GKZ** |
| Status | `enrich_status.py`, `add_hist_status.py`, `build_map_svg.py` | Zustands-Anreicherung, Karte |

Gemeinsame Helfer: `common.py` (I/O, `.env`), `schemas.py` (Pydantic-Modelle),
`blablador_client.py` (LLM-Client), `dbf.py` (abhängigkeitsfreier DBF-Leser).

## Repo-Struktur

```
solve-kg/
├── *.py                     Pipeline-Skripte (flach, damit Importe funktionieren)
├── topology.py              GKZ-Fließtopologie            ← neu
├── region_link.py           Cross-Region-Verknüpfung      ← neu
├── dbf.py                   minimaler DBF-Leser           ← neu
├── ontologie_fein.json      Ontologie (feines Schema)
├── Event_headers.xlsx / relationship_headers.xlsx   Spaltendefinition
├── vkg/                     Ontop-Assets (TTL, OBDA, SQL, Anschluss-README)
├── tests/                   test_cross_region.py (Beweis), test_batch.py, run_parsing_tests.py
├── docs/                    Stand & Testplan
├── requirements.txt / requirements.lock.txt
└── .env.example             Vorlage für Zugangsdaten
```

## Daten & Zugänge (NICHT im Repo)

Per `.gitignore` ausgeschlossen und lokal zu halten:

- **Quell-PDFs**, **WRRL-Fachdaten** (`WRRL/…`, DRAINBASIN/LWSTATUS u. a.), **GeoJSON**.
  Die WRRL-Daten stammen aus dem amtlichen Gewässer-Berichtsportal des Landes.
- **`.env`** mit dem Blablador-Key → aus `.env.example` erzeugen. **Key niemals committen.**
- **`results/`**, **`vkg_export/*.csv`** (abgeleitete Läufe/Fakten).

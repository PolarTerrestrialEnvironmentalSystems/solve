"""Prepare source-faithful Bergwitzsee supplements from saved official sources.

No database writes. Missing values and censored values stay distinguishable.
Annual bathing-water classes and UBA lake assessments are NOT station classes.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path


BASE = "https://ms.sachsen-anhalt.de/themen/gesundheit-und-pflege/daten-zu-gesundheit/badegewaesser/page/"
WATERBODY = "DELW_DEST_EL03OW07-00"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("outputs/bergwitzsee_ergaenzung_20261102/data/monitoring"))
    args = parser.parse_args()
    root = args.directory
    raw = (root / "ms_bergwitzsee.html").read_text(encoding="utf-8")
    sources = []
    for name, url in [
        ("ms_bergwitzsee.html", BASE + "bergwitzsee"),
        ("ms_badegewaesser_methodik.html", BASE + "rechtsgrundlagen-und-ueberwachung"),
        ("ms_bergwitzsee_badegewaesserprofil.pdf", json.loads((root / "ms_profile_download.json").read_text(encoding="utf-8"))["url"]),
        ("uba_texte_68_2016_tagebauseen.pdf", json.loads((root / "uba_download.json").read_text(encoding="utf-8"))["url"]),
    ]:
        data = (root / name).read_bytes()
        sources.append({"file": name, "url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "retrieved_date": "2026-11-02"})

    samples, measurements, missing = [], [], []
    for date_raw, body in re.findall(r'<muse-accordion-item[^>]*label="([^"]+)"[^>]*>(.*?)</muse-accordion-item>', raw, re.S):
        date = datetime.strptime(date_raw.strip(), "%d.%m.%Y").date().isoformat()
        values = [html.unescape(re.sub("<[^>]+>", "", p)).strip() for p in re.findall(r"<p>(.*?)</p>", body, re.S)]
        assert len(values) == 7, (date, values)
        sample_id = f"MS-DEST_PR_0064-{date}"
        samples.append({"sample_id": sample_id, "date": date, "date_precision": "day", "time": None,
                        "waterbody_id": WATERBODY, "monitoring_site_key": "MS:DEST_PR_0064",
                        "source_bathing_water_id": "DEST_PR_0064", "subsite": None,
                        "remark_raw": values[-1], "source_file": "ms_bergwitzsee.html", "source_locator": f"accordion[label={date_raw!r}]"})
        for i in (0, 2, 4):
            match = re.fullmatch(r"\[([^\]]+)\]:\s*(.*)", values[i + 1])
            assert match, values[i + 1]
            unit, value_raw = match.groups()
            row = {"measurement_key": f"{sample_id}:{values[i]}", "sample_id": sample_id, "parameter": values[i],
                   "unit": unit, "value_text": value_raw.strip(), "source_file": "ms_bergwitzsee.html",
                   "source_locator": f"accordion[label={date_raw!r}] / {values[i]}", "method": None}
            if not value_raw.strip():
                missing.append({**row, "reason": "empty_value_in_source"})
                continue
            numeric = re.fullmatch(r"(<)?\s*(\d+(?:[,.]\d+)?)", value_raw.strip())
            assert numeric, value_raw
            qualifier, bound = numeric.groups()
            value = float(bound.replace(",", "."))
            row.update({"value": None if qualifier else value, "qualifier": qualifier or "=",
                        "limit_value": value if qualifier else None, "censored": bool(qualifier)})
            measurements.append(row)
    samples.sort(key=lambda r: r["date"])
    assert len(samples) == 5 and len(measurements) == 14 and len(missing) == 1
    assert sum(row["censored"] for row in measurements) == 7

    classes = []
    for year, value in re.findall(r"Einstufung (\d{4}): ([^<]+)</p>", raw):
        classes.append({"assessment_key": f"MS:DEST_PR_0064:bathing_class:{year}", "waterbody_id": WATERBODY,
                        "assessment_scope": "bathing_water", "source_bathing_water_id": "DEST_PR_0064",
                        "assessment_year": int(year), "assessment_type": "Badegewässerqualität",
                        "value_text": html.unescape(value), "value_numeric": None,
                        "method": "EU-Badegewässerrichtlinie 2006/7/EG; statistische Bewertung mikrobiologischer Ergebnisse von vier Badesaisons",
                        "period_note": "Jahr bezeichnet Einstufung, nicht das Jahr einer einzelnen Probe.",
                        "source_file": "ms_bergwitzsee.html", "source_locator": f"Einstufung {year}"})
    assert len(classes) == 3

    uba_assessments = []
    for name, year, number, label, caveat in [
        ("Phytoplankton", 2013, None, "gut und besser", "Die Quelle gibt ein ökologisches Potenzial an; nicht sehr gut/gut umkodieren."),
        ("Phyto-See-Index", 2013, 1.05, "1,05", "Keine Einheit bzw. Verfahrensversion im Steckbrief angegeben."),
        ("Makrophyten und Phytobenthos", 2013, None, "mäßig", "Basiert auf Makrophytenbewertung; Modul Diatomeen nicht gesichert."),
        ("Ökologisches Potenzial", 2015, None, "mäßig", "2. Bewirtschaftungsplan 2015; keine Einzelprobe."),
        ("Chemischer Zustand ohne Quecksilber in Biota", 2015, None, "gut", "OGewV (2011), Anlage 7. Bundesweit angenommene Hg-Biota-UQN-Überschreitung ausdrücklich nicht einbezogen; keine uneingeschränkte Gesamtchemiebewertung."),
    ]:
        uba_assessments.append({"assessment_key": f"UBA2016:Bergwitzsee:{name}:{year}", "waterbody_id": WATERBODY,
                                "assessment_scope": "waterbody", "assessment_year": year,
                                "assessment_type": name, "value_numeric": number, "value_text": label,
                                "caveat": caveat, "source_file": "uba_texte_68_2016_tagebauseen.pdf", "source_pdf_page": 71,
                                "source_reporting_date": "2016-04-18", "station_id": None})

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "waterbody_id": WATERBODY,
        "sources": sources,
        "monitoring_site": {
            "site_key": "MS:DEST_PR_0064", "name": "Bergwitzsee – Badegewässerüberwachung", "source_bathing_water_id": "DEST_PR_0064",
            "eu_wrrl_station_code": None, "identity_scope": "bathing_water_monitoring_site",
            "waterbody_id": WATERBODY, "source_file": "ms_bergwitzsee_badegewaesserprofil.pdf", "source_pdf_page": 2,
            "source_profile_created_year": 2011, "source_profile_updated_year": 2015,
            "reported_x": 12.5764, "reported_y": 51.7921, "reported_crs_text": "ETRS 89 System",
            "coordinate_interpretation": "Geographische Länge/Breite in Grad; Werteordnung aus Rechtswert/Hochwert; ETRS89 laut Fußnote.",
            "subsites_reported": ["Probenahme 1: Badestrand, Nähe Kiosk", "Probenahme 2: FKK-Strand, Weggabelung"],
            "caveat": "Profil S.2 nennt zwei Probenahmeorte, S.12 eine Entnahmestelle. Aktuelle Terminliste nennt keinen Unterort. Kein LHW-Stationsmatch; Koordinate nicht auf alle Proben als exakt übertragen. DEST_PR_0064 ist Badegewässerkennung, keine WRRL-Messstellenkennung."
        },
        "samples": samples, "measurements": measurements, "missing_values": missing,
        "bathing_water_assessments": classes, "uba_waterbody_assessments": uba_assessments,
        "station_assessments": [],
        "assessment_scope_note": "Keine bislang gefundene Quelle belegt eine separate StationAssessmentV2. UBA Tabelle15 (PDF53, gedruckt49) nennt zwar Bergwitzsee - E4-MP, bewertet laut Überschrift aber Seen. Die Badegewässerklassen gelten für das Badegewässer. Nicht zum Füllen einer leeren Klasse umdeuten.",
        "not_acquired": [
            {"category": "station_specific_biological_assessment", "reason": "Keine eindeutig stationsbezogene Bewertungsdatei aus GLD erhalten; Browserinteraktion stürzte zeitlich ab."},
            {"category": "fish_monitoring_series", "reason": "Keine amtliche quantitative Fisch-Messreihe mit Probenahmezeit/Station beschafft; Artennennungen aus Angelportalen sind kein Ersatz."},
            {"category": "lab_method_details", "reason": "Aktuelle Webseite nennt keine ISO-Laborverfahren, Geräte oder Probentiefe; daher NULL."}
        ],
        "checks": {"samples": len(samples), "numeric_or_censored_measurements": len(measurements), "censored_values": 7,
                   "missing_source_values": len(missing), "bathing_classes": len(classes), "uba_lake_assessments": len(uba_assessments),
                   "station_assessments": 0, "raw_source_hashes": len(sources)}
    }
    (root / "monitoring_supplement.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["checks"], ensure_ascii=False))


if __name__ == "__main__":
    main()

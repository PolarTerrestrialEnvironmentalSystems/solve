"""Conservative, layout-aware extraction of the public eWaBu PDF template.

The original text, table rows and field positions remain in parsed JSON.
No OCR guesses, no legal interpretation, no automatic WaterBody/Station links.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from common import clean

FIELDS = {
    "entries": ["entry_id", "waterbook_number", "title", "authority", "status", "legal_type", "file_reference", "decision_date", "valid_from", "valid_to", "printed_on"],
    "cases": ["case_id", "entry_id", "case_number", "source_page"],
    "uses": ["use_id", "case_id", "entry_id", "use_type", "purpose", "source_page"],
    "sites": ["site_id", "case_id", "entry_id", "name", "water_category", "water_area_code", "municipality", "easting", "northing", "crs", "source_page", "location_raw"],
    "limits": ["limit_id", "case_id", "use_id", "entry_id", "limit_type", "parameter", "operator", "operator_basis", "value", "unit", "statistic", "time_basis", "valid_from", "valid_to", "sampling", "period_raw", "remarks", "source_page", "source_table", "parameter_raw", "value_raw", "row_raw"],
    "review": ["waterbook_number", "warning"],
}
LABELS = {
    "Aktueller Status:": "status", "Wasserbehörde:": "authority", "Wasserbuchblattnr.:": "waterbook_number",
    "Titel:": "title", "Registriernummer/ Aktenzeichen:": "file_reference",
    "Datum der Entscheidung:": "decision_date", "Rechtstitel:": "legal_type",
    "Gültig von:": "valid_from", "Gültig bis:": "valid_to",
    "Nutzungsart:": "use_type", "Nutzungszweck:": "purpose",
    "Bezeichnung:": "name", "Art:": "water_category", "Gewässerkundliche Gebietskennzahl:": "water_area_code",
    "Lage:": "location_raw", "Gemeinde:": "municipality",
}
ENTRY_KEYS = set(FIELDS["entries"]) - {"entry_id", "printed_on"}
DATE_KEYS = {"decision_date", "valid_from", "valid_to", "printed_on"}


def text(value):
    return clean(value).translate(str.maketrans({"‐": "-", "‑": "-", "−": "-", "\u00ad": ""}))


def date(value):
    value = text(value)
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d.%m.%Y").date().isoformat()
    except ValueError:
        return None


def german_number(value):
    value = value.replace(" ", "")
    if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?", value):
        raise ValueError(f"Nicht eindeutig lesbare deutsche Zahl: {value}")
    try:
        result = Decimal(value.replace(".", "").replace(",", "."))
        return float(result)
    except InvalidOperation as exc:
        raise ValueError("Ungültige Zahl") from exc


def parse_value(raw):
    raw = text(raw).replace("≤", "<=").replace("≥", ">=")
    match = re.fullmatch(r"\s*(<=|>=|<|>|=)?\s*([+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?)\s*([^\s]+)\s*", raw)
    if not match:
        return None, None, None
    operator, number, unit = match.groups()
    # Do not turn ranges, dates, footnotes or a second numeric value into units.
    if not re.fullmatch(r"[%°A-Za-zµμ³²/·^0-9*-]+", unit) or not re.search(r"[%°A-Za-zµμ]", unit):
        return None, None, None
    return operator, german_number(number), unit


def limit_row(cells, *, entry_id, case_id, row_number, page, table_kind):
    row = {key: None for key in FIELDS["limits"]}
    cells = [text(c) for c in cells]
    row.update(limit_id=f"{entry_id}:p{page}:{table_kind}:{row_number}", entry_id=entry_id,
               case_id=case_id, source_page=page, source_table=table_kind, row_raw=cells)
    row["parameter_raw"] = cells[0] if cells else ""
    row["parameter"] = row["parameter_raw"]
    row["value_raw"] = cells[1] if len(cells) > 1 else ""
    operator, value, unit = parse_value(row["value_raw"])
    row.update(operator=operator, value=value, unit=unit, operator_basis="explicit" if operator else None)
    low = row["parameter_raw"].lower()
    if "max." in low or "maximal" in low:
        row["statistic"] = "maximum"
        if operator is None and value is not None:
            row.update(operator="<=", operator_basis="source_label_maximum")
    elif "mittl." in low or "mittel" in low:
        row["statistic"] = "mean"
    for needle, basis in [("täglich", "day"), ("jährlich", "year"), ("stündlich", "hour")]:
        if needle in low:
            row["time_basis"] = basis
    if unit and re.fullmatch(r"(?:[µμumk]?g)/(?:l|L|m³|m3)", unit):
        row["limit_type"] = "concentration"
    elif unit and re.fullmatch(r"(?:m³|m3|l|L)/(?:s|min|h|d|a)", unit):
        row["limit_type"] = "quantity"
    else:
        row["limit_type"] = "other"
    if table_kind == "parameter" and len(cells) == 5:
        row.update(valid_from=date(cells[2]), valid_to=date(cells[3]), remarks=cells[4] or None)
    elif table_kind == "extent" and len(cells) == 7:
        row.update(sampling=cells[2] or None, valid_from=date(cells[3]), valid_to=date(cells[4]),
                   period_raw=cells[5] or None, remarks=cells[6] or None)
    return row


def crop(page, bbox):
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return ""
    return text(page.crop(bbox).extract_text(x_tolerance=1, y_tolerance=2) or "")


def label_events(page, tables):
    """Use real label cells to delimit rows, even if value cells were merged."""
    labels = {}
    for table in tables:
        for cell in table.cells:
            label = crop(page, cell)
            if label in LABELS:
                rounded = tuple(round(x, 2) for x in cell)
                labels[rounded] = {"key": LABELS[label], "bbox": cell, "label": label}
    result = []
    for item in labels.values():
        left, top, right, bottom = item["bbox"]
        containing = [t for t in tables if t.bbox[0] <= left + 1 and t.bbox[1] <= top + 1 and t.bbox[3] >= bottom - 1]
        outer_right = max((t.bbox[2] for t in containing), default=page.width - 30)
        next_labels = [other["bbox"][0] for other in labels.values()
                       if other["bbox"][0] >= right - 1 and abs(other["bbox"][1] - top) < 2]
        value_right = min(next_labels) if next_labels else outer_right
        if value_right <= right:
            continue
        value_bbox = (right + 0.2, top + 0.2, value_right - 0.2, bottom - 0.2)
        result.append({"type": "field", "top": top, "left": left, "key": item["key"],
                       "value": crop(page, value_bbox), "label": item["label"], "bbox": list(value_bbox)})
    return result


def table_events(page, tables):
    result = []
    for table_index, table in enumerate(tables):
        first = table.extract()[0]
        kind = {"Parameter": "parameter", "Kriterium": "extent"}.get(text(first[0]))
        if not kind or len(table.rows) < 3:
            continue
        # The two-level header gives boundaries even when PDF data cells merge.
        header_cells = [cell for row in table.rows[:2] for cell in row.cells if cell]
        boundaries = sorted({round(x, 2) for cell in header_cells for x in (cell[0], cell[2])})
        expected_columns = 5 if kind == "parameter" else 7
        if len(boundaries) != expected_columns + 1:
            result.append({"type": "warning", "top": table.bbox[1], "message": f"unrecognized_{kind}_table_columns"})
            continue
        for row_index, row in enumerate(table.rows[2:], 2):
            cells = [cell for cell in row.cells if cell]
            if not cells:
                continue
            top = min(cell[1] for cell in cells)
            following = table.rows[row_index + 1:]
            bottom = min((cell[1] for r in following for cell in r.cells if cell), default=table.bbox[3])
            values = [crop(page, (x + 0.25, top + 0.25, boundaries[i + 1] - 0.25, bottom - 0.25))
                      for i, x in enumerate(boundaries[:-1])]
            if any(values):
                result.append({"type": "limit", "top": top, "kind": kind, "cells": values,
                               "row_number": f"{table_index}-{row_index}", "bbox": [table.bbox[0], top, table.bbox[2], bottom]})
    return result


def parse_pdf(path: Path, expected_number: str):
    import pdfplumber
    entry_id = f"apw:ewabu:{expected_number}"
    result = {"parser_version": 1, "parse_status": "extracted", "warnings": [], "pages": [], "evidence": [],
              "entry": {key: None for key in FIELDS["entries"]}, "cases": [], "uses": [], "sites": [], "limits": []}
    result["entry"].update(entry_id=entry_id, waterbook_number=expected_number)
    current_case = current_use = current_site = None
    expected_counts = []
    found_number = False
    with pdfplumber.open(path) as document:
        if len(document.pages) > 300:
            raise ValueError("Ungewöhnlich großes Wasserbuchblatt; manuelle Prüfung erforderlich.")
        for page_number, page in enumerate(document.pages, 1):
            page_text = page.extract_text() or ""
            result["pages"].append({"page": page_number, "text": page_text})
            numbers = re.findall(r"Wasserbuchblatt(?:nr\.?:?)?\s+(\d+)", page_text)
            if numbers:
                if any(number != expected_number for number in numbers):
                    raise ValueError("Dokumentnummer im PDF stimmt nicht mit dem Download-Link überein.")
                found_number = True
            printed = re.search(r"Zuletzt gedruckt am\s+(\d{2}\.\d{2}\.\d{4})", page_text)
            if printed:
                result["entry"]["printed_on"] = date(printed[1])
            tables = page.find_tables()
            events = label_events(page, tables) + table_events(page, tables)
            for line in page.extract_text_lines():
                content = text(line["text"])
                if match := re.fullmatch(r"(\d+)\.\s*Sachverhalt", content):
                    events.append({"type": "case", "top": line["top"], "number": match[1]})
                elif match := re.fullmatch(r"(\d+)\.2\.(\d+)\.\s*Standort(?:\s+.*)?", content):
                    events.append({"type": "site", "top": line["top"], "case": match[1], "number": match[2]})
                elif match := re.fullmatch(r"(\d+)\.[12]\.\s*(Benutzungstatbestände|Standorte)\s*\((\d+)\)", content):
                    expected_counts.append((match[1], "uses" if match[2] == "Benutzungstatbestände" else "sites", int(match[3])))
                elif match := re.fullmatch(r"(\d+)\.[34]\.\s*(Parameter Überwachung|Kriterien Umfang)\s*\((\d+)\)", content):
                    expected_counts.append((match[1], "parameter" if match[2] == "Parameter Überwachung" else "extent", int(match[3])))
            for event in sorted(events, key=lambda e: (e["top"], e.get("left", 0))):
                result["evidence"].append({"page": page_number, **event})
                kind = event["type"]
                if kind == "case":
                    current_case = f"{entry_id}:case:{event['number']}"
                    current_use = current_site = None
                    if not any(c["case_id"] == current_case for c in result["cases"]):
                        result["cases"].append({"case_id": current_case, "entry_id": entry_id,
                                                "case_number": event["number"], "source_page": page_number})
                elif kind == "site":
                    current_case = f"{entry_id}:case:{event['case']}"
                    current_site = {key: None for key in FIELDS["sites"]}
                    current_site.update(site_id=f"{current_case}:site:{event['number']}", case_id=current_case,
                                        entry_id=entry_id, source_page=page_number)
                    result["sites"].append(current_site)
                elif kind == "warning":
                    result["warnings"].append(f"page_{page_number}:{event['message']}")
                elif kind == "limit":
                    row = limit_row(event["cells"], entry_id=entry_id, case_id=current_case,
                                    row_number=event["row_number"], page=page_number, table_kind=event["kind"])
                    result["limits"].append(row)
                    if row["value"] is None:
                        result["warnings"].append(row["limit_id"] + ":value_not_parsed")
                    date_columns = (2, 3) if event["kind"] == "parameter" else (3, 4)
                    if any(event["cells"][i] and not date(event["cells"][i]) for i in date_columns):
                        result["warnings"].append(row["limit_id"] + ":date_not_parsed")
                elif kind == "field":
                    key, value = event["key"], event["value"] or None
                    if key in ENTRY_KEYS and current_case is None:
                        normalized = date(value) if key in DATE_KEYS else value
                        if value and key in DATE_KEYS and normalized is None:
                            result["warnings"].append(f"entry:{key}:date_not_parsed")
                        result["entry"][key] = normalized
                    elif key == "use_type":
                        current_use = {"use_id": f"{current_case or entry_id}:use:{len(result['uses'])+1}",
                                       "case_id": current_case, "entry_id": entry_id, "use_type": value,
                                       "purpose": None, "source_page": page_number}
                        result["uses"].append(current_use)
                    elif key == "purpose" and current_use is not None:
                        current_use["purpose"] = value
                    elif current_site is not None and key in FIELDS["sites"]:
                        current_site[key] = value
                        if key == "location_raw":
                            current_site["source_page"] = page_number
    if not found_number:
        raise ValueError("Keine passende Blattnummer im Text erkannt (Scan/anderes Layout?). PDF bleibt im Rohdatenordner.")
    if result["entry"]["waterbook_number"] != expected_number:
        raise ValueError("Abweichende Blattnummer im Stammdatenfeld.")
    for site in result["sites"]:
        raw = site.get("location_raw") or ""
        match = re.search(r"Ostwert/Nordwert:\s*([\d.,]+)\s*/\s*([\d.,]+)\s*EPSG:\s*(\d+)", raw)
        if match:
            try:
                site.update(easting=german_number(match[1]), northing=german_number(match[2]), crs="EPSG:" + match[3])
            except ValueError:
                result["warnings"].append(site["site_id"] + ":coordinates_not_parsed")
        else:
            result["warnings"].append(site["site_id"] + ":coordinates_not_parsed")
    for row in result["limits"]:
        uses = [u for u in result["uses"] if u["case_id"] and u["case_id"] == row["case_id"]]
        if len(uses) == 1:
            row["use_id"] = uses[0]["use_id"]
            # Preserve suspicious source wording instead of silently correcting it.
            if "Entnahmemenge" in row["parameter_raw"] and "Einleiten" in (uses[0]["use_type"] or ""):
                result["warnings"].append(row["limit_id"] + ":withdrawal_label_in_discharge_case")
        else:
            result["warnings"].append(row["limit_id"] + ":use_assignment_ambiguous")
    for case_number, kind, count in expected_counts:
        rows = result[kind] if kind in {"uses", "sites"} else [r for r in result["limits"] if r["source_table"] == kind]
        actual = sum(row["case_id"] == f"{entry_id}:case:{case_number}" for row in rows)
        if actual != count:
            result["warnings"].append(f"case_{case_number}:{kind}:expected_{count}:parsed_{actual}")
    for kind, id_field in [("cases", "case_id"), ("uses", "use_id"), ("sites", "site_id"), ("limits", "limit_id")]:
        keys = [r[id_field] for r in result[kind]]
        if len(keys) != len(set(keys)):
            result["warnings"].append(f"{kind}:duplicate_source_keys")
        if kind != "cases" and any(not r["case_id"] for r in result[kind]):
            result["warnings"].append(f"{kind}:case_assignment_missing")
    for required in ("authority", "legal_type", "status"):
        if not result["entry"][required]:
            result["warnings"].append(f"entry:{required}:missing")
    if not result["uses"] or not result["sites"]:
        result["warnings"].append("no_uses_or_sites_extracted")
    if result["warnings"]:
        result["parse_status"] = "review"
    return result

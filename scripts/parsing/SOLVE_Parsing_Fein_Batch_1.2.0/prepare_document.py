"""Page-aware PDF extraction with explicit coverage gaps, never silent OCR claims."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

from common import digest, file_hash, read_json, read_jsonl, write_json, write_jsonl


def parse_pages(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(1, total + 1))
    pages = set()
    try:
        for part in spec.split(","):
            bounds = [int(x.strip()) for x in part.split("-")]
            if len(bounds) == 1:
                pages.add(bounds[0])
            elif len(bounds) == 2 and bounds[0] <= bounds[1]:
                pages.update(range(bounds[0], bounds[1] + 1))
            else:
                raise ValueError
    except ValueError:
        raise ValueError("--pages erwartet PDF-Seiten, z.B. 22,31,46,90-91.") from None
    if not pages or min(pages) < 1 or max(pages) > total:
        raise ValueError(f"PDF-Seiten müssen zwischen 1 und {total} liegen.")
    return sorted(pages)


def normalize_quote(text: str) -> str:
    # Only reversible layout differences, not semantic fuzzy matching.
    text = re.sub(r"(?<=\w)[\-\u00ad]\s*\n\s*(?=\w)", "", text)
    return re.sub(r"\s+", " ", text.replace("\u00ad", "")).strip()


def printed_page(lines: list[dict]) -> str | None:
    for line in lines[:5]:
        text = line["text"].strip()
        if re.fullmatch(r"-?\s*[IVXLCDM]+\s*-?", text):
            return text.strip("- ")
        match = re.search(r"(?:Gewässerentwicklungskonzept.*?)(\d+)\s*$", text)
        if match:
            return match.group(1)
    return None


def ruled_tables(page):
    # Word-generated PDFs often paint a shaded cell background as many rectangles.
    # Their edges are not table borders. Keep actual strokes and thin filled rules.
    grid_page = page.filter(lambda obj: not (
        obj.get("object_type") == "rect" and not obj.get("stroke")
        and obj.get("width", 0) > 2 and obj.get("height", 0) > 2
    ))
    return grid_page.find_tables()


def _split_text(text: str, maximum: int) -> list[str]:
    parts = []
    while len(text) > maximum:
        cut = text.rfind("\n", 0, maximum)
        if cut < maximum // 2:
            cut = text.rfind(" ", 0, maximum)
        if cut <= 0:
            cut = maximum
        parts.append(text[:cut])
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


def make_chunks(blocks: list[dict], max_chars: int = 4000) -> list[dict]:
    """Soft target: include the complete paragraph/row that reaches the target."""
    groups, current, size = [], [], 0
    for block in blocks:
        length = len(block["text"])
        current.append(block)
        size += length
        if size >= max_chars:
            groups.append(current)
            current, size = [], 0
    if current:
        groups.append(current)
    chunks = []
    for index, group in enumerate(groups):
        # Context is explicitly marked and contains full blocks for quotable locators.
        context = []
        if index and len(groups[index - 1][-1]["text"]) <= 2500:
            context.append(groups[index - 1][-1])
        if index + 1 < len(groups) and len(groups[index + 1][0]["text"]) <= 2500:
            context.append(groups[index + 1][0])
        identity = [b["block_id"] for b in group]
        chunks.append({"chunk_id": f"chunk_{index + 1:04d}_{digest(identity)[:8]}",
                       "blocks": group, "context_blocks": context,
                       "pages": sorted({b["page"] for b in group})})
    return chunks


def prepare_document(pdf_path: Path, output: Path, page_spec: str | None,
                     max_chars: int, force: bool = False) -> dict:
    if max_chars < 1500:
        raise ValueError("--chunk-chars muss mindestens 1500 sein.")
    reader = PdfReader(pdf_path)
    selected = parse_pages(page_spec, len(reader.pages))
    fingerprint = digest({"pdf": file_hash(pdf_path), "pages": selected, "max_chars": max_chars,
                          "preparation_version": "paragraphs-1.2.0"})
    cache_path = output / "coverage.json"
    if cache_path.exists() and not force:
        coverage = read_json(cache_path)
        if coverage["preparation_fingerprint"] != fingerprint:
            raise ValueError("Vorhandene PDF-Aufbereitung passt nicht zu diesem Lauf.")
        return {"coverage": coverage, "pages": read_jsonl(output / "pages.jsonl"),
                "blocks": read_jsonl(output / "blocks.jsonl"),
                "tables": read_jsonl(output / "tables.jsonl"),
                "chunks": read_jsonl(output / "chunks.jsonl"),
                "document": read_json(output / "document.json")}

    pages, tables, blocks = [], [], []
    selected_set = set(selected)
    # Cover is always provided as document context, also for a pilot.
    inspect_pages = sorted(selected_set | {1})
    with pdfplumber.open(pdf_path) as pdf:
        margins = Counter()
        for page in pdf.pages:
            text = page.extract_text() or ""
            candidates = set(text.splitlines()[:2] + text.splitlines()[-2:])
            margins.update(t.strip() for t in candidates if len(t.strip()) > 12)
        running_heading = None
        previous_tables: dict[str, str] = {}
        for number in inspect_pages:
            page = pdf.pages[number - 1]
            raw = page.extract_text() or ""
            lines = page.extract_text_lines(return_chars=False)
            label = printed_page(lines)
            issues = []
            if len(raw.strip()) < 100:
                issues.append("little_or_no_text_ocr_not_performed")
            # Decorative logos are not interpreted either; an inventory is not proof of semantic coverage.
            image_regions = [list(i[k] for k in ("x0", "top", "x1", "bottom")) for i in page.images]
            if image_regions:
                issues.append("image_content_not_interpreted")
            if len(page.curves) > 100:
                issues.append("complex_vector_content_not_interpreted")
            page_blocks = []
            try:
                found_tables = ruled_tables(page)
            except Exception as exc:
                found_tables = []
                issues.append("table_extraction_failed:" + type(exc).__name__)
            boxes = []
            current_tables = {}
            for index, table in enumerate(found_tables, 1):
                rows = table.extract()
                # Tiny decorative boxes and text-less drawings are not factual tables.
                if len(rows) < 2 or max((len(r) for r in rows), default=0) < 2:
                    continue
                if not any(any(c and str(c).strip() for c in r) for r in rows):
                    continue
                table_id = f"p{number:04d}_t{index:03d}"
                boxes.append(table.bbox)
                header = rows[0]
                signature = digest(header)
                group = previous_tables.get(signature, table_id)
                current_tables[signature] = group
                table_data = {"table_id": table_id, "table_group": group, "page": number,
                              "printed_page": label, "bbox": list(table.bbox), "rows": rows,
                              "continuation": "matching_header_previous_page" if group != table_id else None}
                tables.append(table_data)
                for row_index, row in enumerate(rows, 1):
                    text = " | ".join(str(c or "") for c in row)
                    if row_index > 1:
                        text = "Spalten: " + " | ".join(str(c or "") for c in header) + "\n" + text
                    page_blocks.append({"text": text, "kind": "table_row", "table_id": table_id,
                                        "table_group": group, "row": row_index, "bbox": list(table.bbox),
                                        "sort_y": table.bbox[1] + row_index / (len(rows) + 1)})
                issues.append("table_structure_requires_spot_check")
            previous_tables = current_tables
            if not boxes and re.search(r"\bTab\.\s*\d+", raw):
                issues.append("table_mention_without_detected_grid_check_text")
            paragraph, bbox = [], None

            def flush():
                nonlocal paragraph, bbox
                if paragraph:
                    page_blocks.append({"text": "\n".join(paragraph), "kind": "text",
                                        "bbox": bbox, "sort_y": bbox[1]})
                paragraph, bbox = [], None

            ignored_margins = []
            for line in lines:
                x0, top, x1, bottom = (line[k] for k in ("x0", "top", "x1", "bottom"))
                txt = line["text"].strip()
                if (top < 75 or bottom > page.height - 65) and margins[txt] >= 3:
                    flush()
                    ignored_margins.append(txt)
                    continue
                if any(b[0] <= (x0+x1)/2 <= b[2] and b[1] <= (top+bottom)/2 <= b[3] for b in boxes):
                    flush()
                    continue
                if bbox and top - bbox[3] > 8:
                    flush()
                if not bbox:
                    bbox = [x0, top, x1, bottom]
                else:
                    bbox = [min(bbox[0], x0), bbox[1], max(bbox[2], x1), bottom]
                paragraph.append(txt)
            flush()
            ordered = []
            for block in sorted(page_blocks, key=lambda b: b["sort_y"]):
                # Preserve layout paragraphs and table rows, independent of target.
                for part in [block["text"]]:
                    current = {k: v for k, v in block.items() if k not in {"sort_y", "text"}}
                    current["text"] = part
                    current.update(page=number, printed_page=label,
                                   block_id=f"p{number:04d}_b{len(ordered)+1:04d}")
                    first = part.splitlines()[0]
                    if re.match(r"^\d+(?:\.\d+)*\.?\s+[A-ZÄÖÜ]", first) and len(first) < 180:
                        running_heading = first
                    current["section"] = running_heading
                    ordered.append(current)
            blocks.extend(ordered)
            pages.append({"page": number, "printed_page": label, "raw_text": raw,
                          "selected": number in selected_set, "context_only": number not in selected_set,
                          "image_regions": image_regions, "curve_count": len(page.curves),
                          "issues": sorted(set(issues)), "ignored_repeated_margins": ignored_margins,
                          "block_ids": [b["block_id"] for b in ordered]})
            print(f"PDF aufbereitet: Seite {number}/{len(reader.pages)}", flush=True)
    chunks = make_chunks([b for b in blocks if b["page"] in selected_set], max_chars)
    coverage = {"preparation_fingerprint": fingerprint, "total_pages": len(reader.pages),
                "selected_pages": selected, "not_selected_pages": sorted(set(range(1, len(reader.pages)+1))-selected_set),
                "chunks": len(chunks), "empty_selected_pages": [p["page"] for p in pages if p["selected"] and not p["block_ids"]],
                "page_issues": {str(p["page"]): p["issues"] for p in pages if p["issues"]},
                "ocr_performed": False, "images_interpreted": False}
    sha = file_hash(pdf_path)
    document = {"document_id": "doc_" + sha[:24], "content_hash": sha,
                "local_file": str(pdf_path.resolve()), "filename": pdf_path.name,
                "title": None, "issuer": None, "authors": None, "url": None,
                "publication_date": None, "retrieved_at": None,
                "pdf_metadata": {str(k): str(v) for k, v in (reader.metadata or {}).items()}}
    write_jsonl(output / "pages.jsonl", pages)
    write_jsonl(output / "blocks.jsonl", blocks)
    write_jsonl(output / "tables.jsonl", tables)
    write_jsonl(output / "chunks.jsonl", chunks)
    write_json(output / "document.json", document)
    write_json(output / "coverage.json", coverage)  # written last: cache is complete
    return {"pages": pages, "blocks": blocks, "tables": tables, "chunks": chunks,
            "coverage": coverage, "document": document}

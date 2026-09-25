"""Link FGG-Elbe station names to the stations in ``messstellen.shp``.

The script deliberately has no GIS dependency.  It reads the DBF attributes
that belong to the point shapefile, builds one row per distinct FGG station,
and writes ranked, auditable match results as CSV files.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import re
import struct
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable


DEFAULT_SOURCE = Path("dummy_data/messstellen.shp")
DEFAULT_FGG_DIR = Path("dummy_data2")
DEFAULT_OUTPUT = Path("output/entity_linking")

GENERIC_WORDS = {
    "an", "am", "auf", "bei", "bruecke", "der", "die", "im", "in",
    "messstelle", "ms", "pegel", "station", "und", "von", "wehr", "zur",
}


@dataclass(frozen=True)
class Station:
    row_number: int
    station_id: str
    name: str
    water_body_id: str
    x: float | None = None
    y: float | None = None


@dataclass(frozen=True)
class WaterBody:
    water_body_id: str
    name: str


def clean_cell(value: str | None) -> str:
    return (value or "").strip().strip("'").strip()


def normalize(value: str) -> str:
    value = clean_cell(value).casefold()
    value = value.translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    value = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def informative_tokens(value: str) -> set[str]:
    return {t for t in normalize(value).split() if len(t) > 1 and t not in GENERIC_WORDS}


def token_set_score(left: str, right: str) -> float:
    a, b = informative_tokens(left), informative_tokens(right)
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    return 100.0 * (2.0 * intersection) / (len(a) + len(b))


def sequence_score(left: str, right: str) -> float:
    a, b = normalize(left), normalize(right)
    return 100.0 * SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def station_name_variants(name: str) -> list[str]:
    variants = {name, re.sub(r"\([^)]*\)", " ", name)}
    variants.update(re.findall(r"\(([^)]*)\)", name))
    return [value for value in variants if normalize(value)]


def similarity(fgg_name: str, source_name: str, waterway: str, water_body: str) -> tuple[float, str]:
    best = (0.0, "")
    for left in station_name_variants(fgg_name):
        for right in station_name_variants(source_name):
            seq = sequence_score(left, right)
            token = token_set_score(left, right)
            containment = 100.0 if normalize(left) in normalize(right) or normalize(right) in normalize(left) else 0.0
            score = max(seq, 0.55 * token + 0.45 * seq, containment)
            reason = f"name(seq={seq:.1f},token={token:.1f},contain={containment:.0f})"
            if score > best[0]:
                best = score, reason

    context = max(token_set_score(waterway, source_name), token_set_score(water_body, source_name))
    # Context can confirm a plausible name, but cannot rescue an unrelated one.
    bonus = min(6.0, context * 0.06) if best[0] >= 45 else 0.0
    return min(100.0, best[0] + bonus), f"{best[1]};context={context:.1f}"


def read_dbf(path: Path, encoding: str = "cp1252") -> list[dict[str, str]]:
    """Read dBASE III/IV fields used by an ESRI shapefile."""
    with path.open("rb") as stream:
        header = stream.read(32)
        if len(header) != 32:
            raise ValueError(f"Invalid DBF header: {path}")
        record_count = struct.unpack("<I", header[4:8])[0]
        header_length = struct.unpack("<H", header[8:10])[0]
        record_length = struct.unpack("<H", header[10:12])[0]
        fields: list[tuple[str, int]] = []
        while stream.tell() < header_length:
            descriptor = stream.read(32)
            if not descriptor or descriptor[0] == 0x0D:
                break
            name = descriptor[:11].split(b"\x00", 1)[0].decode("ascii", errors="replace")
            fields.append((name, descriptor[16]))
        stream.seek(header_length)
        rows: list[dict[str, str]] = []
        for _ in range(record_count):
            record = stream.read(record_length)
            if len(record) < record_length or record[:1] == b"*":
                continue
            offset = 1
            row: dict[str, str] = {}
            for name, length in fields:
                row[name] = record[offset : offset + length].decode(encoding, errors="replace").strip()
                offset += length
            rows.append(row)
    return rows


def first_present(row: dict[str, str], *names: str) -> str:
    folded = {key.casefold(): value for key, value in row.items()}
    for name in names:
        if name.casefold() in folded:
            return clean_cell(folded[name.casefold()])
    return ""


def read_source_stations(shapefile: Path) -> list[Station]:
    rows = read_dbf(shapefile.with_suffix(".dbf"))
    points = read_shp_points(shapefile)
    stations = []
    for index, row in enumerate(rows, start=1):
        stations.append(Station(
            row_number=index,
            station_id=first_present(row, "EU_CD_SM", "EU_CD_STN", "station_id"),
            name=first_present(row, "NAME_STN", "NAME", "station_name"),
            water_body_id=first_present(row, "EU_CD_WB", "EU_CD_LW", "water_body_id"),
            x=points[index - 1][0] if index <= len(points) else None,
            y=points[index - 1][1] if index <= len(points) else None,
        ))
    if not stations or not any(station.name for station in stations):
        raise ValueError("No station-name column (expected NAME_STN) found in the DBF file")
    return stations


def read_shp_points(path: Path) -> list[tuple[float | None, float | None]]:
    points: list[tuple[float | None, float | None]] = []
    with path.open("rb") as stream:
        stream.seek(100)
        while record_header := stream.read(8):
            if len(record_header) != 8:
                raise ValueError(f"Truncated shapefile record header: {path}")
            _, word_length = struct.unpack(">2i", record_header)
            content = stream.read(word_length * 2)
            shape_type = struct.unpack("<i", content[:4])[0]
            points.append(struct.unpack("<2d", content[4:20]) if shape_type == 1 else (None, None))
    return points


def read_water_bodies(path: Path) -> list[WaterBody]:
    return [
        WaterBody(
            water_body_id=first_present(row, "EU_CD_LW", "EU_CD_WB"),
            name=first_present(row, "S_NAME", "NAMETEXT", "NAME"),
        )
        for row in read_dbf(path.with_suffix(".dbf"))
    ]


def detect_csv_encoding(path: Path) -> str:
    raw = path.read_bytes()[:200_000]
    for encoding in ("utf-8-sig", "cp1252", "latin1"):
        try:
            raw.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            pass
    return "latin1"


def find_fgg_csv(directory: Path) -> Path:
    candidates = sorted(
        path for path in directory.glob("*.csv")
        if path.name.casefold() != "fgg_station_reference.csv"
    )
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one CSV in {directory}, found {len(candidates)}")
    return candidates[0]


def read_fgg_stations(path: Path) -> list[dict[str, str]]:
    encoding = detect_csv_encoding(path)
    unique: dict[tuple[str, str, str], dict[str, str]] = {}
    with path.open("r", encoding=encoding, newline="") as stream:
        reader = csv.DictReader(stream, delimiter=";", quotechar="'")
        for row in reader:
            station = clean_cell(row.get("Messstelle"))
            waterway = clean_cell(row.get("Gewässer"))
            water_body = clean_cell(row.get("Wasserkörper"))
            if not station:
                continue
            key = (normalize(station), normalize(waterway), normalize(water_body))
            item = unique.setdefault(key, {
                "fgg_station_name": station,
                "fgg_waterway": waterway,
                "fgg_water_body": water_body,
                "measurement_count": "0",
                "stream_km": clean_cell(row.get("Stromkilometer")),
            })
            item["measurement_count"] = str(int(item["measurement_count"]) + 1)
    for key, item in unique.items():
        digest_input = "|".join(key).encode("utf-8")
        item["fgg_station_key"] = "fgg:" + hashlib.sha256(digest_input).hexdigest()[:16]
    return sorted(unique.values(), key=lambda row: (row["fgg_station_name"], row["fgg_waterway"]))


def read_references(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return {
            normalize(row["fgg_station_name"]): row
            for row in csv.DictReader(stream)
            if clean_cell(row.get("fgg_station_name"))
        }


def classify(score: float, margin: float) -> str:
    if score >= 92 and margin >= 5:
        return "auto_match"
    if score >= 82 and margin >= 10:
        return "auto_match"
    if score >= 65:
        return "review"
    return "unmatched"


def link_stations(
    fgg_rows: Iterable[dict[str, str]], stations: list[Station], water_bodies: list[WaterBody],
    references: dict[str, dict[str, str]], top_n: int,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    mappings: list[dict[str, str]] = []
    candidates: list[dict[str, str]] = []
    for fgg in fgg_rows:
        body_ranked = sorted(
            ((sequence_score(fgg["fgg_water_body"], body.name), body) for body in water_bodies),
            key=lambda item: -item[0],
        )
        body_score, matched_body = body_ranked[0]
        reference = references.get(normalize(fgg["fgg_station_name"]), {})
        ref_x = float(reference["x_etrs89_utm33n"]) if reference.get("x_etrs89_utm33n") else None
        ref_y = float(reference["y_etrs89_utm33n"]) if reference.get("y_etrs89_utm33n") else None
        ranked = []
        for source in stations:
            name_score, name_reason = similarity(
                fgg["fgg_station_name"], source.name,
                fgg["fgg_waterway"], fgg["fgg_water_body"],
            )
            same_body = body_score >= 85 and source.water_body_id == matched_body.water_body_id
            distance = None
            if ref_x is not None and ref_y is not None and source.x is not None and source.y is not None:
                distance = math.hypot(source.x - ref_x, source.y - ref_y)
            if same_body and distance is not None:
                distance_score = max(0.0, 100.0 - distance / 15.0)
                score = 65.0 + 0.35 * distance_score
                reason = f"water_body={body_score:.1f};distance_m={distance:.1f};{name_reason}"
            elif same_body:
                score = max(70.0, 0.3 * name_score + 70.0)
                reason = f"water_body={body_score:.1f};distance_m=;{name_reason}"
            else:
                score = 0.35 * name_score + 0.25 * body_score
                reason = f"water_body={body_score:.1f};different_body;{name_reason}"
            ranked.append((score, source, reason, distance))
        ranked.sort(key=lambda item: (-item[0], item[1].station_id, item[1].row_number))
        best_score, best, reason, best_distance = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        margin = best_score - second_score
        status = classify(best_score, margin)
        # A nearest-neighbour result remains reviewable when the published
        # reference point is not very close; proximity alone is not identity.
        if best_distance is not None and best_distance > 100.0:
            status = "review"
        mappings.append({
            **fgg,
            "source_station_id": best.station_id,
            "source_station_name": best.name,
            "source_water_body_id": best.water_body_id,
            "matched_water_body_name": matched_body.name,
            "water_body_score": f"{body_score:.2f}",
            "distance_m": "" if best_distance is None else f"{best_distance:.2f}",
            "external_station_id": clean_cell(reference.get("external_station_id")),
            "evidence_url": clean_cell(reference.get("source_url")),
            "score": f"{best_score:.2f}",
            "second_best_score": f"{second_score:.2f}",
            "score_margin": f"{margin:.2f}",
            "match_status": status,
            "match_reason": reason,
        })
        for rank, (score, source, candidate_reason, distance) in enumerate(ranked[:top_n], start=1):
            candidates.append({
                "fgg_station_key": fgg["fgg_station_key"],
                "fgg_station_name": fgg["fgg_station_name"],
                "rank": str(rank),
                "source_station_id": source.station_id,
                "source_station_name": source.name,
                "source_water_body_id": source.water_body_id,
                "distance_m": "" if distance is None else f"{distance:.2f}",
                "score": f"{score:.2f}",
                "match_reason": candidate_reason,
            })
    return mappings, candidates


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"Refusing to write an empty result: {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--fgg-dir", type=Path, default=DEFAULT_FGG_DIR)
    parser.add_argument("--water-bodies", type=Path, default=Path("dummy_data/lakewaterbody.shp"))
    parser.add_argument("--reference", type=Path, default=Path("dummy_data2/fgg_station_reference.csv"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--top-n", type=int, default=5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    source_stations = read_source_stations(args.source)
    fgg_path = find_fgg_csv(args.fgg_dir)
    fgg_stations = read_fgg_stations(fgg_path)
    water_bodies = read_water_bodies(args.water_bodies)
    references = read_references(args.reference)
    mappings, candidates = link_stations(
        fgg_stations, source_stations, water_bodies, references, max(1, args.top_n),
    )
    write_csv(args.output_dir / "station_mapping.csv", mappings)
    write_csv(args.output_dir / "station_candidates.csv", candidates)
    counts = Counter(row["match_status"] for row in mappings)
    print(f"Source stations: {len(source_stations)}")
    print(f"Distinct FGG stations: {len(fgg_stations)}")
    print("Statuses: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    print(f"Mapping: {args.output_dir / 'station_mapping.csv'}")
    print(f"Candidates: {args.output_dir / 'station_candidates.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

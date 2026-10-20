#!/usr/bin/env python3
"""Minimaler, abhängigkeitsfreier DBF-Leser (nur Attribute).

Die WRRL-Exporte (DRAINBASIN, LWSTATUS …) liegen als dBASE-.dbf vor. Statt
geopandas/dbfread als Abhängigkeit zu ziehen, lesen wir die Felder direkt.
UTF-8 mit latin1-Fallback; gelöschte Datensätze ('*') werden übersprungen.

    from dbf import read_dbf
    for row in read_dbf("WRRL/current/DRAINBASIN_DEST/DRAINBASIN_DEST.dbf"):
        print(row["EU_CD_WB"], row["GEW_KZ"])
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterator


def read_dbf(path: str | Path) -> Iterator[dict[str, str]]:
    """Iteriere über die Datensätze einer .dbf-Datei als dict{FELD: wert}."""
    data = Path(path).read_bytes()
    n_records = struct.unpack("<I", data[4:8])[0]
    header_len = struct.unpack("<H", data[8:10])[0]
    record_len = struct.unpack("<H", data[10:12])[0]

    fields: list[tuple[str, int]] = []
    pos = 32
    while data[pos] != 0x0D:  # 0x0D terminiert die Felddeklaration
        raw = data[pos:pos + 32]
        name = raw[0:11].split(b"\x00")[0].decode("latin-1")
        length = raw[16]
        fields.append((name, length))
        pos += 32

    for i in range(n_records):
        offset = header_len + i * record_len
        record = data[offset:offset + record_len]
        if not record or record[0:1] == b"*":  # gelöscht
            continue
        values: dict[str, str] = {}
        p = 1  # erstes Byte = Lösch-Flag
        for name, length in fields:
            chunk = record[p:p + length]
            try:
                values[name] = chunk.decode("utf-8").strip()
            except UnicodeDecodeError:
                values[name] = chunk.decode("latin-1").strip()
            p += length
        yield values


def field_names(path: str | Path) -> list[str]:
    """Nur die Feldnamen (für einen schnellen Schema-Blick)."""
    data = Path(path).read_bytes()
    out, pos = [], 32
    while data[pos] != 0x0D:
        out.append(data[pos:pos + 11].split(b"\x00")[0].decode("latin-1"))
        pos += 32
    return out


if __name__ == "__main__":  # kleiner Schema-Dump: python dbf.py <datei.dbf> [N]
    import sys
    p = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    print("Felder:", ", ".join(field_names(p)))
    for k, row in enumerate(read_dbf(p)):
        if k >= n:
            break
        print("---")
        for key, val in row.items():
            if val:
                print(f"  {key} = {val}")

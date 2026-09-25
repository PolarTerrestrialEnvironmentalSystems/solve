"""One bounded, reproducible export; kept separate from the full-run inventory."""
import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from portal import Portal, PortalError, inspect_csv, public_url, write_json


def main():
    args_parser = argparse.ArgumentParser()
    args_parser.add_argument("--accept-terms", action="store_true", required=True)
    args_parser.add_argument("--output", type=Path, required=True)
    args = args_parser.parse_args()
    portal = Portal(accept_terms=args.accept_terms)
    page = portal.enter()
    url = next(url for _, url in page.links() if "ChemWas_start_x" in url)
    page = portal.get_page(url)
    for name, label in [("gewaehltMessstelle", "Potsdam, Humboldtbrücke (Havel)"),
                        ("gewaehltMedium", "Wasser - Gesamtprobe"),
                        ("gewaehltMessjahrVon", "2020"), ("gewaehltMessjahrBis", "2020")]:
        page = portal.choose(page, name, label)
        print("Selected", name, label, flush=True)
    write_json(args.output / "selection.json", page.summary())
    button = next(n for n in page.form().find_all("input") if str(n.attrs.get("name", "")).endswith("_export_tabelle"))
    start = time.monotonic()
    page = portal.submit(page, {button.attrs["name"] + ".x": "1", button.attrs["name"] + ".y": "1",
                                "gewaehltesTabellenformat": "csv", "gewaehlterTabellentyp": "0"}, export=True)
    for index in range(60):
        write_json(args.output / "export_state.json", page.summary())
        print("EXPORT", round(time.monotonic() - start, 1), page.text[:2200], "REFRESH", page.refresh(), flush=True)
        links = [(t, u) for t, u in page.links() if "/ausgabe/" in u and urlsplit(u).path.lower().endswith(".csv")]
        if links:
            results = []
            for title, link in dict.fromkeys(links):
                url, headers, raw = portal.request(link)
                metadata = inspect_csv(raw, headers.get("Content-Type", ""))
                path = args.output / Path(urlsplit(url).path).name
                if path.exists() and path.read_bytes() != raw:
                    raise PortalError("Refusing to overwrite a different pilot CSV")
                path.write_bytes(raw)
                metadata.update({"source_url": public_url(url), "path": str(path), "title": title})
                write_json(path.with_suffix(".metadata.json"), metadata)
                results.append(metadata)
            print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)
            return
        refresh = page.refresh()
        if not refresh:
            raise PortalError("No download link or documented refresh action; see export_state.json")
        time.sleep(max(3, min(refresh[0], 30)))
        page = portal.get_page(refresh[1])
    raise PortalError("Pilot export timed out; not resubmitted")


if __name__ == "__main__":
    main()

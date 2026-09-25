"""Bounded live inspection; no exports are submitted by this command."""
import argparse
import json
import re
from pathlib import Path

from portal import Portal, public_url, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--accept-terms", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    portal = Portal(accept_terms=args.accept_terms)
    page = portal.enter()
    links = [(text, url) for text, url in page.links() if "_start_x.action" in url]
    for _, url in dict.fromkeys(links):
        topic = re.search(r"Untersuchungsbereich(\w+)_start_x", url)[1]
        page = portal.get_page(url)
        summary = page.summary()
        write_json(args.output / (topic + ".json"), summary)
        print(json.dumps({"topic": topic, "url": public_url(page.url),
                          "headings": [n.text() for n in page.root.find_all() if n.tag in {"h1", "h2", "h3", "h4"}],
                          "forms": summary["forms"],
                          "selects": {k: {"n": len(v), "sample": v if len(v) < 15 else v[:3]}
                                      for k, v in summary["selects"].items()},
                          "onchange": summary["onchange"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

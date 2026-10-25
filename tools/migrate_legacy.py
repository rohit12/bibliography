"""One-time migration: pull the data embedded in the original single-file page
(legacy index.html) into data/works.json, data/citations.json, data/books.json.

Usage: python tools/migrate_legacy.py path/to/legacy-index.html
Kept in the repo so the migration is reproducible.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bib  # noqa: E402

TYPE_MAP = {
    "Journal article / chapter": "article",
    "Book / report, dataset": "book",
    "Book chapter": "chapter",
    "Working paper / unpublished": "working-paper",
}

BOOK = {
    "id": "galor-2022",
    "short": "Galor",
    "title": "The Journey of Humanity",
    "author": "Oded Galor",
    "publisher": "Dutton",
    "year": 2022,
    "chapters": {
        "Intro": "Introduction", "Ch 1": "First Steps", "Ch 2": "Lost in Stagnation",
        "Ch 3": "The Storm Beneath the Surface", "Ch 4": "Full Steam",
        "Ch 5": "Metamorphosis", "Ch 6": "The Promised Land",
        "Ch 7": "Splendour and Misery", "Ch 8": "The Fingerprints of Institutions",
        "Ch 9": "The Cultural Factor", "Ch 10": "The Shadow of Geography",
        "Ch 11": "Legacy of the Agricultural Revolution", "Ch 12": "Out of Africa",
        "Coda": "Coda",
    },
}


def main(path):
    html = Path(path).read_text(encoding="utf-8")
    m = re.search(r'<script id="d" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        sys.exit("No embedded data block found.")
    legacy = json.loads(m.group(1))

    refs = []
    for r in legacy["rows"]:
        flags = {}
        for f in r["f"]:
            loc = f.split(" cites ")[0].strip()
            flags[loc] = f[len(loc):].strip()
        refs.append({
            "authors": r["a"].strip(),
            "year": r["y"].strip(),
            "title": r["t"].strip(),
            "venue": r["s"].strip(),
            "type": TYPE_MAP.get(r["k"], r["k"]),
            "citation": r["c"].strip(),
            "cited_in": r["n"],
            "flags": flags,
        })

    book = dict(BOOK)
    book["missing"] = legacy["missing"]
    book["data_sources"] = legacy["data"]
    payload = {"book": book, "references": refs}

    db = bib.DB.empty()
    report = db.add_book(payload)
    db.save()
    print(report.summary())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "legacy-index.html")

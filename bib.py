#!/usr/bin/env python3
"""bib — a cross-book bibliography kept as plain JSON in git.

Layout
  data/      public by construction: works (each with the endnotes citing it), books
  private/   never committed or published: reading verdicts, notes, local extensions
  site/      the page; `build` writes the public data files into site/data/

Run `python bib.py -h` for commands. Standard library only. Also importable:
    import bib; db = bib.DB.load(); db.works["alesina2013origins"]
"""
from __future__ import annotations

import argparse
import csv
import difflib
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
# Set BIB_PRIVATE_DIR to keep private files elsewhere, e.g. a synced folder.
PRIVATE = Path(os.environ["BIB_PRIVATE_DIR"]).expanduser() if os.environ.get("BIB_PRIVATE_DIR") else ROOT / "private"
SITE = ROOT / "site"
SITE_DATA = SITE / "data"

WORKS_F = DATA / "works.json"
BOOKS_F = DATA / "books.json"
NOTES_F = PRIVATE / "notes.json"

def set_root(path) -> None:
    """Point the module at another checkout (used by tests)."""
    global ROOT, DATA, PRIVATE, SITE, SITE_DATA, WORKS_F, BOOKS_F, NOTES_F
    ROOT = Path(path).resolve()
    DATA, PRIVATE, SITE = ROOT / "data", ROOT / "private", ROOT / "site"
    SITE_DATA = SITE / "data"
    WORKS_F, BOOKS_F = DATA / "works.json", DATA / "books.json"
    NOTES_F = PRIVATE / "notes.json"


SCHEMA_VERSION = 1
TYPES = {"article", "book", "chapter", "working-paper", "dataset"}
STATUSES = {"unchecked", "replicated", "contested", "failed-replication", "retracted"}
VERDICTS = {"read", "skimmed", "unread"}

# Only these work fields are ever written to the public site. Anything else
# you add to a work stays in the repo but is never published.
PUBLIC_WORK_FIELDS = [
    "id", "authors", "year", "title", "venue", "type", "citation", "doi",
    "status", "claims", "oa", "cited_in", "listed_in",
]
PUBLIC_OA_FIELDS = ["id", "cited_by_count", "fwci", "is_retracted", "topics", "retrieved"]

STOPWORDS = {
    "a", "an", "the", "of", "on", "in", "and", "or", "to", "for", "from", "with",
    "is", "are", "was", "were", "did", "do", "does", "how", "why", "what", "when",
    "who", "le", "la", "les", "de", "des", "das", "der", "die",
}


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------

def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, obj, compact: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(obj, ensure_ascii=False, indent=1)
    path.write_text(text + "\n", encoding="utf-8")


def fold(s: str) -> str:
    """Strip accents: 'Özak' -> 'Ozak'."""
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_title(t: str) -> str:
    t = fold(t).lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    words = t.split()
    while words and words[0] in {"a", "an", "the"}:
        words = words[1:]
    return " ".join(words)


def surname_key(authors: str) -> str:
    """First author's surname as a comparison key: 'de la Croix, David' -> 'delacroix'."""
    first = fold(authors).split(",")[0]
    first = re.sub(r"\(.*?\)", "", first)
    first = re.sub(r"\bet al\.?", "", first)
    return re.sub(r"[^a-z]", "", first.lower())


def year_int(y) -> int | None:
    m = re.search(r"\d{4}", str(y or ""))
    return int(m.group()) if m else None


def title_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm_title(a), norm_title(b)).ratio()


def make_id(authors: str, year, title: str, taken) -> str:
    sur = surname_key(authors) or "anon"
    y = year_int(year) or "nd"
    words = [w for w in norm_title(title).split() if w not in STOPWORDS]
    word = words[0] if words else ""
    base = f"{sur}{y}{word}"[:48]
    if base not in taken:
        return base
    for suffix in "bcdefghijklmnopqrstuvwxyz":
        if base + suffix not in taken:
            return base + suffix
    raise ValueError(f"cannot make unique id for {base}")


def loc_key(loc: str, chapter_order: list[str]):
    """'Ch 10 n51' -> (index of 'Ch 10' in chapter order, 51)."""
    if " n" in loc:
        chap, note = loc.rsplit(" n", 1)
    else:
        chap, note = loc, "0"
    idx = chapter_order.index(chap) if chap in chapter_order else 999
    n = int(note) if note.isdigit() else 0
    return (idx, n, loc)


def parse_authors(s: str) -> list[dict]:
    """Best-effort split of 'Surname, Given, Given2 Surname2 and Given3 Surname3'
    into [{'family','given'}]. Author strings are as printed in each book."""
    s = s.strip()
    out: list[dict] = []
    others = False
    if re.search(r"\bet al\.?$", s):
        others = True
        s = re.sub(r",?\s*et al\.?$", "", s)
    if "," not in s:
        return [{"literal": s}] + ([{"literal": "others"}] if others else [])
    family, rest = s.split(",", 1)
    pieces = [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", rest) if p.strip()]
    given = pieces[0] if pieces else ""
    given = re.sub(r"\s*\(eds?\.\)\s*", "", given)
    out.append({"family": family.strip(), "given": given})
    for p in pieces[1:]:
        p = re.sub(r"\s*\(eds?\.\)\s*", "", p)
        parts = p.split()
        if len(parts) == 1:
            out.append({"family": parts[0], "given": ""})
        else:
            out.append({"family": parts[-1], "given": " ".join(parts[:-1])})
    if others:
        out.append({"literal": "others"})
    return out


# ----------------------------------------------------------------------------
# the database
# ----------------------------------------------------------------------------

@dataclass
class ImportReport:
    book: str
    new: list = field(default_factory=list)
    matched: list = field(default_factory=list)
    review: list = field(default_factory=list)
    citations: int = 0

    def summary(self) -> str:
        lines = [
            f"Book {self.book}: {len(self.new)} new works, {len(self.matched)} matched existing, "
            f"{len(self.review)} need review, {self.citations} citations recorded.",
        ]
        for wid, how in self.matched[:40]:
            lines.append(f"  = {wid}  ({how})")
        if len(self.matched) > 40:
            lines.append(f"  … and {len(self.matched) - 40} more matches")
        for wid, other, why in self.review:
            lines.append(f"  ? {wid} may duplicate {other}  ({why})")
        return "\n".join(lines)


class DB:
    def __init__(self, works: list, citations: list, books: list):
        self.works = {w["id"]: w for w in works}
        self.citations = citations
        self.books = {b["id"]: b for b in books}

    # -- io --------------------------------------------------------------
    @classmethod
    def load(cls) -> "DB":
        return cls.from_files(load_json(WORKS_F, []), load_json(BOOKS_F, []))

    @classmethod
    def from_files(cls, raw_works: list, raw_books: list) -> "DB":
        """On disk each work carries its own citations (cited_in) and the books
        whose References list it (listed_in). In memory these are flattened."""
        works, cites, refs = [], [], {}
        for w in raw_works:
            w = dict(w)
            for c in w.pop("cited_in", []):
                cites.append({"work": w["id"], **c})
            for bid in w.pop("listed_in", []):
                refs.setdefault(bid, []).append(w["id"])
            works.append(w)
        books = []
        for b in raw_books:
            b = dict(b)
            b["references"] = refs.get(b["id"], [])
            books.append(b)
        return cls(works, cites, books)

    @classmethod
    def empty(cls) -> "DB":
        return cls([], [], [])

    def chapter_order(self, book_id: str) -> list[str]:
        return list(self.books.get(book_id, {}).get("chapters", {}).keys())

    def to_files(self) -> tuple[list, list]:
        orders = {b: self.chapter_order(b) for b in self.books}
        cited: dict[str, list] = {}
        for c in sorted(self.citations,
                        key=lambda c: (c["book"], loc_key(c["loc"], orders.get(c["book"], [])))):
            cited.setdefault(c["work"], []).append({k: c[k] for k in ("book", "loc", "flag") if k in c})
        listed: dict[str, list] = {}
        for b in self.books.values():
            for wid in b.get("references", []):
                listed.setdefault(wid, []).append(b["id"])
        works = []
        for w in sorted(self.works.values(), key=lambda w: w["id"]):
            out = {k: v for k, v in w.items() if k not in ("cited_in", "listed_in")}
            out["cited_in"] = cited.get(w["id"], [])
            out["listed_in"] = sorted(listed.get(w["id"], []))
            works.append(out)
        books = [{k: v for k, v in b.items() if k != "references"}
                 for b in sorted(self.books.values(), key=lambda b: b["id"])]
        return works, books

    def save(self):
        works, books = self.to_files()
        dump_json(WORKS_F, works)
        dump_json(BOOKS_F, books)

    # -- derived views ---------------------------------------------------
    def cites_by_work(self) -> dict[str, list]:
        out: dict[str, list] = {}
        for c in self.citations:
            out.setdefault(c["work"], []).append(c)
        return out

    def books_for(self, wid: str) -> list[str]:
        ids = {c["book"] for c in self.citations if c["work"] == wid}
        ids |= {b["id"] for b in self.books.values() if wid in b.get("references", [])}
        return sorted(ids)

    # -- matching --------------------------------------------------------
    def find_match(self, ref: dict):
        """Return (work_id, how, is_confident) or (None, None, None)."""
        doi = (ref.get("doi") or "").lower()
        if doi:
            for w in self.works.values():
                if (w.get("doi") or "").lower() == doi:
                    return w["id"], "doi", True
        sk = surname_key(ref["authors"])
        y = year_int(ref.get("year"))
        best = (0.0, None)
        for w in self.works.values():
            if surname_key(w["authors"]) != sk:
                continue
            r = title_ratio(ref["title"], w["title"])
            if r > best[0]:
                best = (r, w)
        ratio, w = best
        if not w:
            return None, None, None
        wy = year_int(w.get("year"))
        gap = abs(y - wy) if (y and wy) else 0
        is_book = "book" in (ref.get("type"), w.get("type"))
        is_wp = "working-paper" in (ref.get("type"), w.get("type"))
        if ratio >= 0.93 and (gap <= 1 or (is_book and ratio >= 0.97)):
            return w["id"], f"title {ratio:.2f}, year gap {gap}", True
        if ratio >= 0.93 and is_wp and gap <= 6:
            return w["id"], f"working paper vs published? title {ratio:.2f}, year gap {gap}", False
        if ratio >= 0.80:
            return w["id"], f"similar title {ratio:.2f}, year gap {gap}", False
        return None, None, None

    # -- import ----------------------------------------------------------
    def add_book(self, payload: dict, dry_run: bool = False) -> ImportReport:
        book = dict(payload["book"])
        bid = book["id"]
        if bid in self.books:
            raise SystemExit(f"Book {bid} already exists. Remove it first with `bib.py remove-book {bid}`.")
        report = ImportReport(bid)
        listed: list[str] = []
        for ref in payload["references"]:
            ref = {k: v for k, v in ref.items()}
            ref.setdefault("type", "article")
            wid, how, confident = self.find_match(ref)
            if wid and confident:
                report.matched.append((wid, how))
                w = self.works[wid]
                if ref.get("doi") and not w.get("doi"):
                    w["doi"] = ref["doi"]
            else:
                new_id = make_id(ref["authors"], ref.get("year"), ref["title"], self.works)
                w = {
                    "id": new_id,
                    "authors": ref["authors"],
                    "year": str(ref.get("year") or ""),
                    "title": ref["title"],
                    "venue": ref.get("venue", ""),
                    "type": ref["type"],
                    "citation": ref.get("citation", ""),
                    "doi": ref.get("doi"),
                    "status": "unchecked",
                    "claims": [],
                }
                if wid:  # uncertain match: keep separate, flag for review
                    w["possible_duplicate_of"] = wid
                    report.review.append((new_id, wid, how))
                self.works[new_id] = w
                report.new.append(new_id)
                wid = new_id
            if wid not in listed:
                listed.append(wid)
            flags = ref.get("flags", {})
            for loc in ref.get("cited_in", []):
                c = {"work": wid, "book": bid, "loc": loc}
                if flags.get(loc):
                    c["flag"] = flags[loc]
                self.citations.append(c)
                report.citations += 1
        book["references"] = listed
        book.setdefault("missing", [])
        book.setdefault("data_sources", [])
        book["added"] = date.today().isoformat()
        self.books[bid] = book
        if dry_run:
            # roll back by reloading from disk
            fresh = DB.load()
            self.works, self.citations, self.books = fresh.works, fresh.citations, fresh.books
        return report

    def remove_book(self, bid: str):
        if bid not in self.books:
            raise SystemExit(f"No book {bid}")
        del self.books[bid]
        self.citations = [c for c in self.citations if c["book"] != bid]
        still_used = {c["work"] for c in self.citations}
        for b in self.books.values():
            still_used |= set(b.get("references", []))
        orphans = [w for w in self.works if w not in still_used]
        for w in orphans:
            del self.works[w]
        return orphans

    def merge(self, keep: str, drop: str):
        """Fold work `drop` into `keep` (for duplicates flagged at import)."""
        for x in (keep, drop):
            if x not in self.works:
                raise SystemExit(f"No work {x}")
        k, d = self.works[keep], self.works[drop]
        for fld in ("doi", "venue", "citation"):
            if not k.get(fld) and d.get(fld):
                k[fld] = d[fld]
        k["claims"] = sorted(set(k.get("claims", [])) | set(d.get("claims", [])))
        for c in self.citations:
            if c["work"] == drop:
                c["work"] = keep
        for b in self.books.values():
            refs = b.get("references", [])
            if drop in refs:
                refs = [keep if r == drop else r for r in refs]
                b["references"] = list(dict.fromkeys(refs))
        for w in self.works.values():
            if w.get("possible_duplicate_of") == drop:
                w["possible_duplicate_of"] = keep
        if k.get("possible_duplicate_of") in (keep, drop):
            k.pop("possible_duplicate_of")
        del self.works[drop]
        notes = load_json(NOTES_F, {})
        if drop in notes:
            notes.setdefault(keep, {}).update({kk: v for kk, v in notes.pop(drop).items() if v})
            dump_json(NOTES_F, notes)

    # -- checks ----------------------------------------------------------
    def validate(self) -> list[str]:
        errs = []
        for wid, w in self.works.items():
            if not re.fullmatch(r"[a-z0-9]+", wid):
                errs.append(f"work id not a plain slug: {wid}")
            for req in ("authors", "title", "type"):
                if not w.get(req):
                    errs.append(f"{wid}: missing {req}")
            if w.get("type") not in TYPES:
                errs.append(f"{wid}: unknown type {w.get('type')}")
            if w.get("status", "unchecked") not in STATUSES:
                errs.append(f"{wid}: unknown status {w.get('status')}")
            if w.get("possible_duplicate_of") and w["possible_duplicate_of"] not in self.works:
                errs.append(f"{wid}: possible_duplicate_of points nowhere")
        for c in self.citations:
            if c["work"] not in self.works:
                errs.append(f"citation to unknown work {c['work']} ({c['book']} {c['loc']})")
            if c["book"] not in self.books:
                errs.append(f"citation from unknown book {c['book']}")
        for b in self.books.values():
            for r in b.get("references", []):
                if r not in self.works:
                    errs.append(f"book {b['id']} lists unknown work {r}")
        for wid in load_json(NOTES_F, {}):
            if wid not in self.works:
                errs.append(f"private note for unknown work {wid}")
        return errs


# ----------------------------------------------------------------------------
# OpenAlex enrichment
# ----------------------------------------------------------------------------

OA_BASE = "https://api.openalex.org"
OA_SELECT = "id,doi,display_name,publication_year,cited_by_count,fwci,is_retracted,topics,authorships,type"


def oa_get(path: str, params: dict, _opener=None) -> dict:
    key = os.environ.get("OPENALEX_API_KEY")
    if key:
        params = {**params, "api_key": key}
    url = f"{OA_BASE}{path}?{urllib.parse.urlencode(params)}"
    opener = _opener or urllib.request.urlopen
    for attempt in range(5):
        try:
            with opener(url, timeout=30) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("OpenAlex: too many retries")


def oa_summary(rec: dict) -> dict:
    return {
        "id": (rec.get("id") or "").rsplit("/", 1)[-1],
        "cited_by_count": rec.get("cited_by_count"),
        "fwci": rec.get("fwci"),
        "is_retracted": rec.get("is_retracted"),
        "topics": [t["display_name"] for t in (rec.get("topics") or [])[:3]],
        "retrieved": date.today().isoformat(),
    }


def oa_accept(w: dict, rec: dict) -> float:
    """Score an OpenAlex record against a work; 0 means reject."""
    r = title_ratio(w["title"], rec.get("display_name") or "")
    if r < 0.88:
        return 0.0
    sk = surname_key(w["authors"])
    names = [surname_key(fold(a["author"]["display_name"]).split()[-1] + ",")
             for a in rec.get("authorships", []) if a.get("author", {}).get("display_name")]
    if sk and names and sk not in names and not any(sk.endswith(n) or n.endswith(sk) for n in names if n):
        return 0.0
    y, ry = year_int(w.get("year")), rec.get("publication_year")
    if w["type"] != "book" and y and ry and abs(y - ry) > 3:
        return 0.0
    return r


def enrich(db: DB, limit: int | None = None, force: bool = False, save_every: int = 25, _get=oa_get):
    todo = [w for w in db.works.values() if force or not w.get("oa")]
    todo = [w for w in todo if w["type"] != "dataset"]
    if limit:
        todo = todo[:limit]
    matched, unmatched, newly_retracted = 0, [], []

    with_doi = [w for w in todo if w.get("doi")]
    for i in range(0, len(with_doi), 50):
        batch = with_doi[i:i + 50]
        dois = "|".join(w["doi"].lower().removeprefix("https://doi.org/") for w in batch)
        res = _get("/works", {"filter": f"doi:{dois}", "per_page": 50, "select": OA_SELECT})
        by_doi = {(r.get("doi") or "").lower().removeprefix("https://doi.org/"): r for r in res.get("results", [])}
        for w in batch:
            rec = by_doi.get(w["doi"].lower().removeprefix("https://doi.org/"))
            if rec:
                w["oa"] = oa_summary(rec)
                matched += 1
            else:
                unmatched.append(w["id"])

    without = [w for w in todo if not w.get("doi")]
    for n, w in enumerate(without, 1):
        q = re.sub(r"[,:|]", " ", w["title"])
        params = {"search": q, "per_page": 5, "select": OA_SELECT}
        y = year_int(w.get("year"))
        if y and w["type"] != "book":
            params["filter"] = f"publication_year:{y - 3}-{y + 3}"
        try:
            res = _get("/works", params)
        except urllib.error.HTTPError as e:
            print(f"  ! {w['id']}: HTTP {e.code}", file=sys.stderr)
            unmatched.append(w["id"])
            continue
        scored = sorted(((oa_accept(w, r), r) for r in res.get("results", [])), key=lambda t: -t[0])
        if scored and scored[0][0] > 0:
            rec = scored[0][1]
            w["oa"] = oa_summary(rec)
            if rec.get("doi") and not w.get("doi"):
                w["doi"] = rec["doi"].removeprefix("https://doi.org/")
            matched += 1
        else:
            unmatched.append(w["id"])
        if n % save_every == 0:
            db.save()
            print(f"  … {n}/{len(without)} searched", file=sys.stderr)

    for w in todo:
        if (w.get("oa") or {}).get("is_retracted") and w.get("status") != "retracted":
            w["status"] = "retracted"
            newly_retracted.append(w["id"])
    db.save()
    return matched, unmatched, newly_retracted


# ----------------------------------------------------------------------------
# exports
# ----------------------------------------------------------------------------

BIBTEX_TYPE = {"article": "article", "book": "book", "chapter": "incollection",
               "working-paper": "techreport", "dataset": "misc"}


def bibtex_escape(s: str) -> str:
    return re.sub(r"([&%$#_])", r"\\\1", s or "").replace("{", "\\{").replace("}", "\\}")


def to_bibtex(works) -> str:
    out = []
    for w in works:
        names = []
        for a in parse_authors(w["authors"]):
            names.append("{" + a["literal"] + "}" if "literal" in a and a["literal"] != "others"
                         else "others" if a.get("literal") == "others"
                         else f"{a['family']}, {a['given']}".strip(", "))
        fields = {"author": " and ".join(names), "title": "{" + bibtex_escape(w["title"]) + "}",
                  "year": w.get("year", "")}
        venue_key = {"article": "journal", "book": "publisher", "chapter": "booktitle",
                     "working-paper": "institution", "dataset": "howpublished"}[w["type"]]
        if w.get("venue"):
            fields[venue_key] = bibtex_escape(w["venue"])
        if w.get("doi"):
            fields["doi"] = w["doi"]
        body = ",\n".join(f"  {k} = {{{v}}}" if not v.startswith("{") else f"  {k} = {v}"
                          for k, v in fields.items() if v)
        out.append(f"@{BIBTEX_TYPE[w['type']]}{{{w['id']},\n{body}\n}}")
    return "\n\n".join(out) + "\n"


def to_csv(db: DB, works) -> str:
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["id", "authors", "year", "title", "venue", "type", "doi", "status",
                 "books", "endnote_citations", "openalex_cited_by", "citation"])
    cbw = db.cites_by_work()
    for w in works:
        wr.writerow([w["id"], w["authors"], w.get("year"), w["title"], w.get("venue"), w["type"],
                     w.get("doi") or "", w.get("status", ""), ";".join(db.books_for(w["id"])),
                     len(cbw.get(w["id"], [])), (w.get("oa") or {}).get("cited_by_count", ""),
                     w.get("citation", "")])
    return buf.getvalue()


def to_csl(works) -> list:
    cmap = {"article": "article-journal", "book": "book", "chapter": "chapter",
            "working-paper": "report", "dataset": "dataset"}
    out = []
    for w in works:
        item = {"id": w["id"], "type": cmap[w["type"]], "title": w["title"],
                "author": [a for a in parse_authors(w["authors"]) if a.get("literal") != "others"]}
        y = year_int(w.get("year"))
        if y:
            item["issued"] = {"date-parts": [[y]]}
        if w.get("venue"):
            item["container-title" if w["type"] in ("article", "chapter") else "publisher"] = w["venue"]
        if w.get("doi"):
            item["DOI"] = w["doi"]
        out.append(item)
    return out


# ----------------------------------------------------------------------------
# build (public site data) + leak guard
# ----------------------------------------------------------------------------

def public_work(w: dict) -> dict:
    pw = {k: w[k] for k in PUBLIC_WORK_FIELDS if k in w and w[k] not in (None, "")}
    if "oa" in pw:
        pw["oa"] = {k: v for k, v in pw["oa"].items() if k in PUBLIC_OA_FIELDS}
    return pw


def private_strings() -> set[str]:
    found: set[str] = set()

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str) and len(x) >= 12:
            found.add(x)

    for p in PRIVATE.glob("*.json"):
        walk(load_json(p, None))
    # work and book ids are public by design; private files merely point at them
    public_ids = {w["id"] for w in load_json(WORKS_F, [])} | {b["id"] for b in load_json(BOOKS_F, [])}
    return found - public_ids


def leak_check(site_dir: Path | None = None) -> list[str]:
    site_dir = site_dir or SITE
    problems = []
    secrets = private_strings()
    private_names = {f.name for f in PRIVATE.glob("*") if f.is_file() and f.name != "README.md"}
    for p in site_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.name in private_names:
            problems.append(f"private file in site: {p.relative_to(ROOT)}")
        if p.suffix not in {".json", ".html", ".bib", ".csv", ".js", ".css", ".txt", ".md"}:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for s in secrets:
            if s in text:
                problems.append(f"private text found in {p.relative_to(ROOT)}: {s[:40]!r}")
                break
    for p in site_dir.rglob("works.json"):
        for w in load_json(p, []):
            extra = set(w) - set(PUBLIC_WORK_FIELDS)
            if extra:
                problems.append(f"non-public fields {sorted(extra)} in {p.relative_to(ROOT)}")
                break
    return problems


def build(db: DB) -> dict:
    file_works, file_books = db.to_files()
    works = [public_work(w) for w in file_works]
    for f in SITE_DATA.glob("*"):
        if f.is_file():
            f.unlink()
    dump_json(SITE_DATA / "works.json", works, compact=True)
    dump_json(SITE_DATA / "books.json", file_books, compact=True)
    raw = sorted(db.works.values(), key=lambda w: w["id"])
    (SITE_DATA / "bibliography.bib").write_text(to_bibtex(raw), encoding="utf-8")
    (SITE_DATA / "bibliography.csv").write_text(to_csv(db, raw), encoding="utf-8")
    meta = {"schema": SCHEMA_VERSION, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "works": len(works), "citations": len(db.citations), "books": len(db.books)}
    dump_json(SITE_DATA / "meta.json", meta)
    problems = leak_check()
    if problems:
        for p in problems:
            print("LEAK:", p, file=sys.stderr)
        for f in SITE_DATA.glob("*"):
            if f.is_file():
                f.unlink()
        raise SystemExit("Build refused: private data would be published.")
    return meta


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def fmt_work(w: dict, width: int = 90) -> str:
    line = f"{w['id']:<28} {w.get('year', ''):<5} {w['authors'].split(',')[0][:18]:<18} {w['title']}"
    return line[:width]


def cmd_validate(db, a):
    errs = db.validate()
    for e in errs:
        print("ERROR:", e)
    dups = [w for w in db.works.values() if w.get("possible_duplicate_of")]
    for w in dups:
        print(f"REVIEW: {w['id']} may duplicate {w['possible_duplicate_of']}  "
              f"(merge: bib.py merge {w['possible_duplicate_of']} {w['id']}, or clear: bib.py not-dup {w['id']})")
    print(f"{len(db.works)} works, {len(db.citations)} citations, {len(db.books)} books — "
          f"{'OK' if not errs else f'{len(errs)} errors'}")
    return 1 if errs else 0


def cmd_stats(db, a):
    cbw = db.cites_by_work()
    print(f"{len(db.works)} works · {len(db.citations)} citations · {len(db.books)} books")
    for b in db.books.values():
        cited = {c['work'] for c in db.citations if c['book'] == b['id']}
        refs = set(b.get("references", []))
        print(f"  {b['id']:<18} {b['title'][:40]:<40} refs {len(refs):>4}  cited {len(cited):>4}  "
              f"listed-not-cited {len(refs - cited):>3}")
    multi = [w for w in db.works.values() if len(db.books_for(w['id'])) > 1]
    print(f"Works in 2+ books: {len(multi)}")
    for w in sorted(multi, key=lambda w: (-len(db.books_for(w['id'])), -len(cbw.get(w['id'], []))))[:a.top]:
        print(f"  {len(db.books_for(w['id']))} books  {fmt_work(w)}")
    status = {}
    for w in db.works.values():
        status[w.get("status", "unchecked")] = status.get(w.get("status", "unchecked"), 0) + 1
    print("Status:", ", ".join(f"{k} {v}" for k, v in sorted(status.items())))
    enriched = sum(1 for w in db.works.values() if w.get("oa"))
    print(f"OpenAlex-enriched: {enriched}/{len(db.works)} · with DOI: "
          f"{sum(1 for w in db.works.values() if w.get('doi'))}")
    print("Most cited in endnotes:")
    for w in sorted(db.works.values(), key=lambda w: -len(cbw.get(w['id'], [])))[:a.top]:
        print(f"  {len(cbw.get(w['id'], [])):>3}  {fmt_work(w)}")


def cmd_search(db, a):
    q = fold(" ".join(a.query)).lower()
    hits = [w for w in db.works.values()
            if q in fold(f"{w['id']} {w['authors']} {w['title']} {w.get('venue', '')} {w.get('year', '')}").lower()]
    for w in sorted(hits, key=lambda w: w["id"]):
        print(fmt_work(w))
    print(f"{len(hits)} match(es)")


def cmd_show(db, a):
    w = db.works.get(a.id)
    if not w:
        raise SystemExit(f"No work {a.id}. Try: bib.py search …")
    print(json.dumps(w, ensure_ascii=False, indent=2))
    for c in db.cites_by_work().get(a.id, []):
        print(f"  cited: {c['book']} {c['loc']}" + (f"  [{c['flag']}]" if c.get("flag") else ""))
    note = load_json(NOTES_F, {}).get(a.id)
    if note:
        print("  private:", json.dumps(note, ensure_ascii=False))


def cmd_add_book(db, a):
    payload = load_json(Path(a.file), None)
    if payload is None:
        raise SystemExit(f"Cannot read {a.file}")
    report = db.add_book(payload, dry_run=a.dry_run)
    print(report.summary())
    if a.dry_run:
        print("(dry run — nothing written)")
    else:
        db.save()


def cmd_remove_book(db, a):
    orphans = db.remove_book(a.book)
    db.save()
    print(f"Removed {a.book}; dropped {len(orphans)} works no longer referenced by any book.")


def cmd_merge(db, a):
    db.merge(a.keep, a.drop)
    db.save()
    print(f"Merged {a.drop} into {a.keep}.")


def cmd_not_dup(db, a):
    w = db.works.get(a.id) or sys.exit(f"No work {a.id}")
    w.pop("possible_duplicate_of", None)
    db.save()
    print(f"{a.id} marked as distinct.")


def cmd_tag(db, a):
    w = db.works.get(a.id) or sys.exit(f"No work {a.id}")
    if a.status:
        if a.status not in STATUSES:
            raise SystemExit(f"status must be one of {sorted(STATUSES)}")
        w["status"] = a.status
    if a.claim:
        w["claims"] = sorted(set(w.get("claims", [])) | set(a.claim))
    if a.unclaim:
        w["claims"] = [c for c in w.get("claims", []) if c not in a.unclaim]
    if a.doi:
        w["doi"] = a.doi.removeprefix("https://doi.org/")
    db.save()
    if a.verdict or a.note is not None:
        notes = load_json(NOTES_F, {})
        n = notes.setdefault(a.id, {})
        if a.verdict:
            if a.verdict not in VERDICTS:
                raise SystemExit(f"verdict must be one of {sorted(VERDICTS)}")
            n["verdict"] = a.verdict
        if a.note is not None:
            n["note"] = a.note
        dump_json(NOTES_F, notes)
    print(f"Updated {a.id}.")


def cmd_claims(db, a):
    claims: dict[str, list] = {}
    for w in db.works.values():
        for c in w.get("claims", []):
            claims.setdefault(c, []).append(w)
    for c, ws in sorted(claims.items(), key=lambda kv: -len(kv[1])):
        tag = "  ← single source" if len(ws) == 1 else ""
        print(f"{len(ws):>3}  {c}{tag}")
        if a.verbose:
            for w in ws:
                print(f"       {fmt_work(w)}  [{w.get('status')}]")


def cmd_enrich(db, a):
    if not os.environ.get("OPENALEX_API_KEY"):
        print("Note: OPENALEX_API_KEY not set — keyless use gets a tenth of the free daily budget.",
              file=sys.stderr)
    m, un, ret = enrich(db, limit=a.limit, force=a.force)
    print(f"Matched {m}; unmatched {len(un)}.")
    if ret:
        print("Newly marked retracted:", ", ".join(ret))
    if un and a.verbose:
        for wid in un:
            print("  unmatched:", fmt_work(db.works[wid]))


def cmd_export(db, a):
    works = sorted(db.works.values(), key=lambda w: w["id"])
    if a.book:
        keep = {c["work"] for c in db.citations if c["book"] == a.book} | set(db.books[a.book].get("references", []))
        works = [w for w in works if w["id"] in keep]
    if a.format == "bibtex":
        text = to_bibtex(works)
    elif a.format == "csv":
        text = to_csv(db, works)
    else:
        text = json.dumps(to_csl(works), ensure_ascii=False, indent=1) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"Wrote {len(works)} works to {a.out}")
    else:
        sys.stdout.write(text)


def cmd_build(db, a):
    errs = db.validate()
    if errs:
        for e in errs:
            print("ERROR:", e, file=sys.stderr)
        raise SystemExit("Build refused: fix validation errors first.")
    meta = build(db)
    print(f"Built site/data: {meta['works']} works, {meta['citations']} citations, {meta['books']} books. No leaks.")


def cmd_serve(db, a):
    import functools
    import http.server
    cmd_build(db, a)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    print(f"Serving http://localhost:{a.port}  (Ctrl+C to stop)")
    http.server.HTTPServer(("127.0.0.1", a.port), handler).serve_forever()


def load_extensions(sub, handlers: dict) -> None:
    """Load ext.py from the private folder, if present. It must define
    register(subparsers, handlers) to add its own commands. Being in private/,
    it is never committed or published."""
    ext = PRIVATE / "ext.py"
    if not ext.exists():
        return
    import importlib.util
    # so `import bib` inside the extension gets this module, even when run as a script
    sys.modules.setdefault("bib", sys.modules[__name__])
    spec = importlib.util.spec_from_file_location("bib_ext", ext)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bib_ext"] = mod
    spec.loader.exec_module(mod)
    mod.register(sub, handlers)


def main(argv=None):
    p = argparse.ArgumentParser(prog="bib.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate", help="check data integrity and list possible duplicates")
    s = sub.add_parser("stats", help="counts, cross-book overlap, most-cited")
    s.add_argument("--top", type=int, default=15)
    s = sub.add_parser("search", help="search works by author, title, venue, year")
    s.add_argument("query", nargs="+")
    s = sub.add_parser("show", help="full record for one work, including private notes")
    s.add_argument("id")

    s = sub.add_parser("add-book", help="import a book's references (see inbox/TEMPLATE.json)")
    s.add_argument("file")
    s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("remove-book", help="remove a book and any works only it referenced")
    s.add_argument("book")
    s = sub.add_parser("merge", help="fold a duplicate work into another")
    s.add_argument("keep")
    s.add_argument("drop")
    s = sub.add_parser("not-dup", help="clear a possible-duplicate flag")
    s.add_argument("id")

    s = sub.add_parser("tag", help="set status/claims (public) or verdict/note (private)")
    s.add_argument("id")
    s.add_argument("--status", help=" | ".join(sorted(STATUSES)))
    s.add_argument("--claim", action="append", help="claim this work is used to support (repeatable)")
    s.add_argument("--unclaim", action="append")
    s.add_argument("--doi")
    s.add_argument("--verdict", help="private: " + " | ".join(sorted(VERDICTS)))
    s.add_argument("--note", help="private note")
    s = sub.add_parser("claims", help="list claims and how many works support each")
    s.add_argument("-v", "--verbose", action="store_true")

    s = sub.add_parser("enrich", help="add DOI, citation counts, topics, retractions from OpenAlex")
    s.add_argument("--limit", type=int)
    s.add_argument("--force", action="store_true", help="re-fetch works already enriched")
    s.add_argument("-v", "--verbose", action="store_true")

    s = sub.add_parser("export", help="export BibTeX, CSV or CSL-JSON")
    s.add_argument("format", choices=["bibtex", "csv", "csl"])
    s.add_argument("--book")
    s.add_argument("-o", "--out")

    sub.add_parser("build", help="write public data into site/data (refuses on leaks)")
    s = sub.add_parser("serve", help="build and preview the site locally")
    s.add_argument("--port", type=int, default=8000)

    handlers = {"validate": cmd_validate, "stats": cmd_stats, "search": cmd_search, "show": cmd_show,
                "add-book": cmd_add_book, "remove-book": cmd_remove_book, "merge": cmd_merge,
                "not-dup": cmd_not_dup, "tag": cmd_tag, "claims": cmd_claims, "enrich": cmd_enrich,
                "export": cmd_export, "build": cmd_build, "serve": cmd_serve}
    load_extensions(sub, handlers)

    a = p.parse_args(argv)
    db = DB.load()
    return handlers[a.cmd](db, a) or 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # e.g. piped into `head`
        sys.stderr.close()
        sys.exit(0)

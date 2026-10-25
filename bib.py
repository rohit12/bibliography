#!/usr/bin/env python3
"""bib — a cross-book bibliography kept as plain JSON in git.

Layout
  data/      public by construction:
               works.json     each work, with the endnotes citing it
               books.json     each book and its chapters
               endnotes.json  one paraphrased claim per endnote, with cause→effect
                              links between concepts and the groups it is about
               theories.json  named chains of links (a book's argument, or a synthesis)
               concepts.json, groups.json   the vocabularies links and groups use
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
ENDNOTES_F = DATA / "endnotes.json"
THEORIES_F = DATA / "theories.json"
CONCEPTS_F = DATA / "concepts.json"
GROUPS_F = DATA / "groups.json"
NOTES_F = PRIVATE / "notes.json"

def set_root(path) -> None:
    """Point the module at another checkout (used by tests)."""
    global ROOT, DATA, PRIVATE, SITE, SITE_DATA, WORKS_F, BOOKS_F, NOTES_F
    global ENDNOTES_F, THEORIES_F, CONCEPTS_F, GROUPS_F
    ROOT = Path(path).resolve()
    DATA, PRIVATE, SITE = ROOT / "data", ROOT / "private", ROOT / "site"
    SITE_DATA = SITE / "data"
    WORKS_F, BOOKS_F = DATA / "works.json", DATA / "books.json"
    ENDNOTES_F, THEORIES_F = DATA / "endnotes.json", DATA / "theories.json"
    CONCEPTS_F, GROUPS_F = DATA / "concepts.json", DATA / "groups.json"
    NOTES_F = PRIVATE / "notes.json"


SCHEMA_VERSION = 2
TYPES = {"article", "book", "chapter", "working-paper", "dataset"}
STATUSES = {"unchecked", "replicated", "contested", "failed-replication", "retracted"}
VERDICTS = {"read", "skimmed", "unread"}
# How a link reads: raises, lowers, no effect, rises-then-falls, related (direction unstated)
SIGNS = {"+": "raises", "-": "lowers", "0": "no effect on", "~": "has a hump-shaped effect on", "?": "is linked to"}
# Optional evidence type for a work, set with `tag --method`
METHODS = {"experiment", "natural-experiment", "regression", "ethnography", "history",
           "dataset", "model", "review", "genetics", "archaeology"}
GROUP_KINDS = {"place", "religion", "social"}

# Only these work fields are ever written to the public site. Anything else
# you add to a work stays in the repo but is never published.
PUBLIC_WORK_FIELDS = [
    "id", "authors", "year", "title", "venue", "type", "citation", "doi",
    "status", "method", "claims", "oa", "cited_in", "listed_in",
]
PUBLIC_ENDNOTE_FIELDS = ["book", "loc", "claim", "links", "about"]
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
    def __init__(self, works: list, citations: list, books: list,
                 endnotes: list | None = None, theories: list | None = None,
                 concepts: list | None = None, groups: list | None = None):
        self.works = {w["id"]: w for w in works}
        self.citations = citations
        self.books = {b["id"]: b for b in books}
        self.endnotes = list(endnotes or [])
        self.theories = list(theories or [])
        self.concepts = {c["id"]: c for c in (concepts or [])}
        self.groups = {g["id"]: g for g in (groups or [])}

    # -- io --------------------------------------------------------------
    @classmethod
    def load(cls) -> "DB":
        db = cls.from_files(load_json(WORKS_F, []), load_json(BOOKS_F, []))
        db.endnotes = load_json(ENDNOTES_F, [])
        db.theories = load_json(THEORIES_F, [])
        db.concepts = {c["id"]: c for c in load_json(CONCEPTS_F, [])}
        db.groups = {g["id"]: g for g in load_json(GROUPS_F, [])}
        return db

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

    def save_endnotes(self):
        orders = {b: self.chapter_order(b) for b in self.books}
        self.endnotes.sort(key=lambda n: (n["book"], loc_key(n["loc"], orders.get(n["book"], []))))
        lines = [json.dumps({k: n[k] for k in PUBLIC_ENDNOTE_FIELDS if n.get(k)}, ensure_ascii=False)
                 for n in self.endnotes]
        # one endnote per line keeps diffs readable
        ENDNOTES_F.parent.mkdir(parents=True, exist_ok=True)
        ENDNOTES_F.write_text("[\n" + ",\n".join(lines) + "\n]\n", encoding="utf-8")

    def import_endnotes(self, book: str, rows: list, replace: bool = True) -> list[str]:
        """Load claim rows {loc, claim, links?, about?} for one book. Returns problems;
        nothing is changed if there are any."""
        if book not in self.books:
            return [f"no book {book}"]
        probs, seen, clean = [], set(), []
        for i, r in enumerate(rows, 1):
            loc, claim = (r.get("loc") or "").strip(), (r.get("claim") or "").strip()
            if not loc or not claim:
                probs.append(f"row {i}: needs loc and claim")
                continue
            if loc in seen:
                probs.append(f"{loc}: duplicate")
            seen.add(loc)
            n = {"book": book, "loc": loc, "claim": claim}
            if r.get("links"):
                n["links"] = [list(lk) for lk in r["links"]]
            if r.get("about"):
                n["about"] = list(dict.fromkeys(r["about"]))
            probs += self._endnote_problems(n)
            clean.append(n)
        if probs:
            return probs
        keep = [n for n in self.endnotes if n["book"] != book] if replace else \
               [n for n in self.endnotes if not (n["book"] == book and n["loc"] in seen)]
        self.endnotes = keep + clean
        return []

    def _endnote_problems(self, n: dict) -> list[str]:
        where = f"{n.get('book')} {n.get('loc')}"
        out = []
        for lk in n.get("links", []):
            if len(lk) != 3:
                out.append(f"{where}: link must be [from, to, sign]")
                continue
            a, b, s = lk
            for x in (a, b):
                if self.concepts and x not in self.concepts:
                    out.append(f"{where}: unknown concept {x}")
            if s not in SIGNS:
                out.append(f"{where}: unknown sign {s!r}")
        for g in n.get("about", []):
            if self.groups and g not in self.groups:
                out.append(f"{where}: unknown group {g}")
        return out

    # -- derived views ---------------------------------------------------
    def cites_by_work(self) -> dict[str, list]:
        out: dict[str, list] = {}
        for c in self.citations:
            out.setdefault(c["work"], []).append(c)
        return out

    def works_at(self) -> dict[tuple, list]:
        """(book, loc) -> work ids cited in that endnote."""
        out: dict[tuple, list] = {}
        for c in self.citations:
            out.setdefault((c["book"], c["loc"]), []).append(c["work"])
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

    def refresh_book(self, payload: dict, dry_run: bool = False) -> list:
        """Add citations found in a re-extracted payload to works this book already lists.
        Never removes anything; returns [(work_id, loc)] added."""
        bid = payload["book"]["id"]
        if bid not in self.books:
            raise SystemExit(f"No book {bid}; use add-book.")
        listed = set(self.books[bid].get("references", []))
        have = {(c["work"], c["loc"]) for c in self.citations if c["book"] == bid}
        added = []
        for ref in payload["references"]:
            wid, _, confident = self.find_match(dict(ref, type=ref.get("type", "article")))
            if not (wid and confident and wid in listed):
                continue
            for loc in ref.get("cited_in", []):
                if (wid, loc) not in have:
                    have.add((wid, loc))
                    added.append((wid, loc))
                    if not dry_run:
                        self.citations.append({"work": wid, "book": bid, "loc": loc})
        return added

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
        for w in self.works.values():
            if w.get("method") and w["method"] not in METHODS:
                errs.append(f"{w['id']}: unknown method {w['method']}")
        for c in self.concepts.values():
            if not c.get("label"):
                errs.append(f"concept {c['id']}: missing label")
        for g in self.groups.values():
            if g.get("kind") not in GROUP_KINDS:
                errs.append(f"group {g['id']}: kind must be one of {sorted(GROUP_KINDS)}")
        seen = set()
        for n in self.endnotes:
            key = (n.get("book"), n.get("loc"))
            if key in seen:
                errs.append(f"duplicate endnote {key[0]} {key[1]}")
            seen.add(key)
            if n.get("book") not in self.books:
                errs.append(f"endnote from unknown book {n.get('book')}")
            if not n.get("claim"):
                errs.append(f"endnote {key[0]} {key[1]}: missing claim")
            errs += self._endnote_problems(n)
        tids = set()
        for t in self.theories:
            tid = t.get("id", "?")
            if tid in tids:
                errs.append(f"duplicate theory {tid}")
            tids.add(tid)
            for b in t.get("books", []):
                if b not in self.books:
                    errs.append(f"theory {tid}: unknown book {b}")
            if not t.get("steps"):
                errs.append(f"theory {tid}: no steps")
            for st in t.get("steps", []):
                errs += [e.replace(" None", "") for e in
                         self._endnote_problems({"book": f"theory {tid}", "loc": None, "links": [st]})]
        return errs


# ----------------------------------------------------------------------------
# analysis: links, pillars, connections, comparisons
#
# An endnote's links say what the book claims at that point ("plough lowers
# gender_norms"). The works cited in the endnote are its evidence. Everything
# below is derived from those two facts, so it works for any set of books.
# ----------------------------------------------------------------------------

OPPOSED = {("+", "-"), ("-", "+"), ("+", "0"), ("0", "+"), ("-", "0"), ("0", "-")}


def link_index(db: DB) -> dict:
    """(from, to) -> {"notes": [(book, loc, sign)], "books": {book: {sign: count}}}."""
    idx: dict = {}
    for n in db.endnotes:
        for a, b, s in n.get("links", []):
            e = idx.setdefault((a, b), {"notes": [], "books": {}})
            e["notes"].append((n["book"], n["loc"], s))
            bs = e["books"].setdefault(n["book"], {})
            bs[s] = bs.get(s, 0) + 1
    return idx


def surnames(w: dict) -> set[str]:
    out = set()
    for p in parse_authors(w.get("authors", "")):
        name = p.get("family") or (p.get("literal") if p.get("literal") != "others" else "")
        key = re.sub(r"[^a-z]", "", fold(name or "").lower())
        if key:
            out.add(key)
    return out


def author_clusters(db: DB, wids) -> list[list[str]]:
    """Group works that share any author: each group counts as one independent source."""
    wids = [w for w in dict.fromkeys(wids) if w in db.works]
    parent = {w: w for w in wids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner: dict[str, str] = {}
    for w in wids:
        for s in surnames(db.works[w]):
            if s in owner:
                parent[find(w)] = find(owner[s])
            else:
                owner[s] = w
    groups: dict[str, list] = {}
    for w in wids:
        groups.setdefault(find(w), []).append(w)
    return sorted(groups.values(), key=len, reverse=True)


def evidence(w: dict) -> dict:
    oa = w.get("oa") or {}
    return {"cites": oa.get("cited_by_count"), "fwci": oa.get("fwci"), "status": w.get("status", "unchecked"),
            "method": w.get("method"), "type": w.get("type"),
            "weak": w.get("status") in {"contested", "failed-replication", "retracted"} or bool(oa.get("is_retracted"))}


def step_support(db: DB, step, books=None, idx=None, at=None) -> dict:
    """Endnotes and works behind one link. Support comes from the theory's own books;
    the same link stated in other books is corroboration; opposite signs are challenges."""
    idx = idx if idx is not None else link_index(db)
    at = at if at is not None else db.works_at()
    a, b, s = step
    own, also, against = [], [], []
    for book, loc, sign in idx.get((a, b), {}).get("notes", []):
        if sign == s:
            (own if (not books or book in books) else also).append((book, loc))
        elif (sign, s) in OPPOSED:
            against.append((book, loc, sign))
    works = list(dict.fromkeys(w for k in own for w in at.get(k, [])))
    return {"from": a, "to": b, "sign": s, "notes": own, "also": also, "against": against,
            "works": works, "sources": len(author_clusters(db, works))}


def pillars(db: DB, theory: dict) -> dict:
    """Which works hold a theory up.

    load      sum over steps of the work's share of that step's support
    critical  steps that would lose all support without this work
    steps     how many steps the work supports"""
    idx, at = link_index(db), db.works_at()
    books = theory.get("books") or None
    steps = [step_support(db, st, books, idx, at) for st in theory["steps"]]
    per: dict[str, dict] = {}
    for i, st in enumerate(steps):
        n = len(st["works"])
        for w in st["works"]:
            p = per.setdefault(w, {"id": w, "load": 0.0, "steps": [], "critical": []})
            p["load"] += 1 / n
            p["steps"].append(i)
            if n == 1:
                p["critical"].append(i)
        # a single author cluster makes every work in it critical together
        clusters = author_clusters(db, st["works"])
        if len(clusters) == 1 and n > 1:
            for w in clusters[0]:
                if i not in per[w]["critical"]:
                    per[w]["critical"].append(i)
    ranked = sorted(per.values(), key=lambda p: (-len(p["critical"]), -p["load"], p["id"]))
    for p in ranked:
        p["load"] = round(p["load"], 3)
        p.update(evidence(db.works[p["id"]]))
    return {"id": theory["id"], "steps": steps, "pillars": ranked,
            "unsupported": [i for i, st in enumerate(steps) if not st["works"]],
            "fragile": [i for i, st in enumerate(steps) if 0 < st["sources"] <= 1]}


def book_pillars(db: DB, book: str, top: int = 25) -> list[dict]:
    """Works ranked by how much of a book's linked argument rests on them."""
    at = db.works_at()
    load: dict[str, float] = {}
    for n in db.endnotes:
        if n["book"] != book or not n.get("links"):
            continue
        ws = at.get((book, n["loc"]), [])
        for w in ws:
            load[w] = load.get(w, 0) + len(n["links"]) / len(ws)
    out = [{"id": w, "load": round(v, 3), **evidence(db.works[w])} for w, v in load.items() if w in db.works]
    return sorted(out, key=lambda p: (-p["load"], p["id"]))[:top]


def connections(db: DB, min_support: int = 1, top: int = 60) -> dict:
    """Bridges, shared and contested links, rival explanations and candidate chains across books."""
    idx = link_index(db)
    books = sorted(db.books)
    concept_books: dict[str, dict] = {}
    for n in db.endnotes:
        for a, b, _ in n.get("links", []):
            for c in {a, b}:
                concept_books.setdefault(c, {}).setdefault(n["book"], set()).add(n["loc"])
    bridges = sorted(({"concept": c, "books": {b: len(v) for b, v in bs.items()}}
                      for c, bs in concept_books.items() if len(bs) > 1),
                     key=lambda x: (-min(x["books"].values()), x["concept"]))

    shared, contested = [], []
    for (a, b), e in idx.items():
        signs = {s for bs in e["books"].values() for s in bs}
        if len(e["books"]) > 1:
            shared.append({"from": a, "to": b, "books": e["books"]})
        if any((x, y) in OPPOSED for x in signs for y in signs):
            contested.append({"from": a, "to": b, "books": e["books"],
                              "notes": [list(t) for t in e["notes"]]})
    shared.sort(key=lambda x: (-len(x["books"]), -sum(sum(v.values()) for v in x["books"].values())))
    contested.sort(key=lambda x: -len(x["notes"]))

    # rivals: the same outcome explained by different causes in different books
    causes: dict[str, dict] = {}
    for (a, b), e in idx.items():
        for bk, ss in e["books"].items():
            n = sum(v for s, v in ss.items() if s != "?")
            if n:
                causes.setdefault(b, {}).setdefault(bk, {})[a] = n
    rivals = []
    for target, per in causes.items():
        if len(per) < 2:
            continue
        sets = {bk: set(v) for bk, v in per.items()}
        common = set.intersection(*sets.values())
        only = {bk: sorted(s - set().union(*(o for k, o in sets.items() if k != bk)),
                           key=lambda c: -per[bk][c]) for bk, s in sets.items()}
        if all(only.values()):
            rivals.append({"outcome": target, "shared": sorted(common), "only": only,
                           "weight": sum(sum(v.values()) for v in per.values())})
    rivals.sort(key=lambda r: -r["weight"])

    # candidate chains: A→B in one book, B→C in another, A→C stated nowhere
    def strong(e, bk):
        ss = e["books"].get(bk, {})
        best = max((s for s in ss if s in "+-"), key=lambda s: ss[s], default=None)
        return (best, ss[best]) if best else (None, 0)

    out_edges: dict[str, list] = {}
    for (a, b), e in idx.items():
        for bk in e["books"]:
            s, n = strong(e, bk)
            if s and n >= min_support:
                out_edges.setdefault(a, []).append((b, bk, s, n))
    chains = []
    for a, firsts in out_edges.items():
        for b, bk1, s1, n1 in firsts:
            for c, bk2, s2, n2 in out_edges.get(b, []):
                if bk2 == bk1 or c == a or (a, c) in idx:
                    continue
                chains.append({"path": [a, b, c], "books": [bk1, bk2], "signs": [s1, s2],
                               "sign": "+" if s1 == s2 else "-", "support": min(n1, n2)})
    chains.sort(key=lambda x: (-x["support"], x["path"]))

    # works cited in more than one book, with what each book uses them for
    at = db.works_at()
    uses: dict[str, dict] = {}
    for n in db.endnotes:
        for w in at.get((n["book"], n["loc"]), []):
            uses.setdefault(w, {}).setdefault(n["book"], []).append(n["loc"])
    shared_works = sorted(({"id": w, "books": u} for w, u in uses.items() if len(u) > 1),
                          key=lambda x: (-len(x["books"]), -sum(len(v) for v in x["books"].values())))
    return {"books": books, "bridges": bridges[:top], "shared": shared[:top], "contested": contested[:top],
            "rivals": rivals[:top], "chains": chains[:top], "shared_works": shared_works[:top]}


def gaps(db: DB) -> dict:
    """Where the evidence is thin: theory steps with one source, groups only one book covers,
    concepts only one book uses."""
    fragile = []
    for t in db.theories:
        p = pillars(db, t)
        for i in p["unsupported"] + p["fragile"]:
            st = p["steps"][i]
            fragile.append({"theory": t["id"], "step": [st["from"], st["to"], st["sign"]],
                            "works": st["works"], "sources": st["sources"]})
    by_group: dict[str, dict] = {}
    by_concept: dict[str, dict] = {}
    for n in db.endnotes:
        for g in n.get("about", []):
            by_group.setdefault(g, {}).setdefault(n["book"], 0)
            by_group[g][n["book"]] += 1
        for a, b, _ in n.get("links", []):
            for c in {a, b}:
                by_concept.setdefault(c, {}).setdefault(n["book"], 0)
                by_concept[c][n["book"]] += 1
    nbooks = len(db.books)
    one_book = lambda d: sorted(({"id": k, "books": v} for k, v in d.items() if len(v) == 1 and nbooks > 1),
                                key=lambda x: -sum(x["books"].values()))
    return {"fragile_steps": fragile, "groups_one_book": one_book(by_group)[:40],
            "concepts_one_book": one_book(by_concept)[:40],
            "groups_untouched": sorted(set(db.groups) - set(by_group))}


def compare(db: DB, groups: list[str] | None = None) -> dict:
    """Group × concept grid: which endnotes say something about a group and a mechanism."""
    groups = groups or sorted(db.groups)
    cells: dict[str, dict] = {g: {} for g in groups}
    for n in db.endnotes:
        about = [g for g in n.get("about", []) if g in cells]
        if not about:
            continue
        concepts = {c for a, b, _ in n.get("links", []) for c in (a, b)} or {"_none"}
        for g in about:
            for c in concepts:
                cells[g].setdefault(c, []).append([n["book"], n["loc"]])
    return {"groups": groups, "cells": cells}


def analysis(db: DB) -> dict:
    return {"theories": {t["id"]: pillars(db, t) for t in db.theories},
            "books": {b: book_pillars(db, b) for b in db.books},
            "connections": connections(db), "gaps": gaps(db)}


def link_text(db: DB, a: str, b: str, s: str) -> str:
    lab = lambda c: db.concepts.get(c, {}).get("label", c)
    return f"{lab(a)} {SIGNS.get(s, s)} {lab(b)}"


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
    for name, allowed in (("works.json", PUBLIC_WORK_FIELDS), ("endnotes.json", PUBLIC_ENDNOTE_FIELDS)):
        for p in site_dir.rglob(name):
            for row in load_json(p, []):
                extra = set(row) - set(allowed)
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
    notes = [{k: n[k] for k in PUBLIC_ENDNOTE_FIELDS if n.get(k)} for n in db.endnotes]
    dump_json(SITE_DATA / "endnotes.json", notes, compact=True)
    dump_json(SITE_DATA / "theories.json", db.theories, compact=True)
    dump_json(SITE_DATA / "concepts.json", list(db.concepts.values()), compact=True)
    dump_json(SITE_DATA / "groups.json", list(db.groups.values()), compact=True)
    dump_json(SITE_DATA / "analysis.json", analysis(db), compact=True)
    raw = sorted(db.works.values(), key=lambda w: w["id"])
    (SITE_DATA / "bibliography.bib").write_text(to_bibtex(raw), encoding="utf-8")
    (SITE_DATA / "bibliography.csv").write_text(to_csv(db, raw), encoding="utf-8")
    meta = {"schema": SCHEMA_VERSION, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "works": len(works), "citations": len(db.citations), "books": len(db.books),
            "endnotes": len(notes), "theories": len(db.theories), "signs": SIGNS}
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


def cmd_refresh_book(db, a):
    payload = load_json(Path(a.file), None)
    added = db.refresh_book(payload, dry_run=a.dry_run)
    for wid, loc in added:
        print(f"  + {wid}  {loc}")
    print(f"{len(added)} citations {'would be ' if a.dry_run else ''}added.")
    if not a.dry_run:
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
    if a.method:
        if a.method not in METHODS:
            raise SystemExit(f"method must be one of {sorted(METHODS)}")
        w["method"] = a.method
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


def read_rows(path: Path) -> list:
    """JSON list or JSON Lines."""
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def cmd_import_endnotes(db, a):
    rows = [r for f in a.files for r in read_rows(Path(f))]
    probs = db.import_endnotes(a.book, rows, replace=not a.merge)
    for pr in probs[:40]:
        print("ERROR:", pr)
    if probs:
        raise SystemExit(f"{len(probs)} problems; nothing written.")
    db.save_endnotes()
    mine = [n for n in db.endnotes if n["book"] == a.book]
    print(f"{a.book}: {len(mine)} endnotes, {sum(len(n.get('links', [])) for n in mine)} links, "
          f"{sum(1 for n in mine if n.get('about'))} tagged with groups.")


def cmd_theories(db, a):
    for t in db.theories:
        p = pillars(db, t)
        print(f"{t['id']:<24} {len(t['steps']):>2} steps  {len(p['unsupported'])} unsupported  "
              f"{len(p['fragile'])} single-source  {t['title']}")


def cmd_pillars(db, a):
    if a.book:
        for p in book_pillars(db, a.book, a.top):
            print(f"{p['load']:>6.2f}  {p['cites'] or '':>6}  {fmt_work(db.works[p['id']], 80)}")
        return
    t = next((t for t in db.theories if t["id"] == a.theory), None)
    if not t:
        raise SystemExit("Theory ids: " + ", ".join(t["id"] for t in db.theories))
    p = pillars(db, t)
    print(t["title"])
    for i, st in enumerate(p["steps"]):
        flag = "  ✗ no support" if not st["works"] else ("  ! one source" if st["sources"] == 1 else "")
        print(f" {i+1:>2}. {link_text(db, st['from'], st['to'], st['sign'])}"
              f"  [{len(st['notes'])} notes, {len(st['works'])} works, {st['sources']} sources"
              f"{', +' + str(len(st['also'])) + ' elsewhere' if st['also'] else ''}"
              f"{', ' + str(len(st['against'])) + ' against' if st['against'] else ''}]{flag}")
    print("Pillars (load = share of support, ★ = a step depends on it alone):")
    for q in p["pillars"][:a.top]:
        star = "★" * len(q["critical"])
        print(f"  {q['load']:>5.2f} {star:<3} {fmt_work(db.works[q['id']], 80)}"
              + (f"  [{q['status']}]" if q["weak"] else ""))


def cmd_connections(db, a):
    c = connections(db)
    lab = lambda x: db.concepts.get(x, {}).get("label", x)
    sec = a.section
    if sec in (None, "shared"):
        print("Links stated in more than one book:")
        for e in c["shared"][:a.top]:
            print(f"  {lab(e['from'])} → {lab(e['to'])}  {e['books']}")
    if sec in (None, "contested"):
        print("Links with conflicting signs:")
        for e in c["contested"][:a.top]:
            print(f"  {lab(e['from'])} → {lab(e['to'])}  {e['books']}")
    if sec in (None, "rivals"):
        print("Same outcome, different causes:")
        for r in c["rivals"][:a.top]:
            print(f"  {lab(r['outcome'])}: " + " | ".join(f"{b}: {', '.join(v[:4])}" for b, v in r["only"].items()))
    if sec in (None, "chains"):
        print("Candidate chains across books (A→B in one, B→C in another, A→C in none):")
        for ch in c["chains"][:a.top]:
            x, y, z = ch["path"]
            print(f"  {lab(x)} {ch['signs'][0]}→ {lab(y)} {ch['signs'][1]}→ {lab(z)}  "
                  f"({ch['books'][0]} / {ch['books'][1]}, support {ch['support']})")
    if sec in (None, "works"):
        print("Works both books lean on:")
        for w in c["shared_works"][:a.top]:
            print(f"  {fmt_work(db.works[w['id']], 70)}  " + ", ".join(f"{b} {len(v)}" for b, v in w["books"].items()))


def cmd_compare(db, a):
    g = compare(db, a.groups or None)
    lab = lambda x: db.concepts.get(x, {}).get("label", x)
    concepts = sorted({c for row in g["cells"].values() for c in row}, key=lambda c: -sum(
        len(g["cells"][r].get(c, [])) for r in g["groups"]))[:a.top]
    for gid in g["groups"]:
        row = g["cells"][gid]
        print(f"{db.groups.get(gid, {}).get('label', gid)}: {sum(len(v) for v in row.values())} mentions")
        for c in concepts:
            if row.get(c):
                print(f"   {lab(c):<40} " + ", ".join(f"{b} {l}" for b, l in row[c][:6]))


def cmd_gaps(db, a):
    g = gaps(db)
    lab = lambda x: db.concepts.get(x, {}).get("label", x)
    print("Theory steps with no support or a single source:")
    for f in g["fragile_steps"]:
        print(f"  {f['theory']}: {link_text(db, *f['step'])}  ({len(f['works'])} works, {f['sources']} sources)")
    print("Groups only one book discusses:")
    for x in g["groups_one_book"][:a.top]:
        print(f"  {x['id']:<20} {x['books']}")
    print("Concepts only one book uses:")
    for x in g["concepts_one_book"][:a.top]:
        print(f"  {lab(x['id']):<40} {x['books']}")


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
    print(f"Built site/data: {meta['works']} works, {meta['citations']} citations, {meta['books']} books, "
          f"{meta['endnotes']} endnotes, {meta['theories']} theories. No leaks.")


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
    s = sub.add_parser("refresh-book", help="add newly found citations from a re-extracted book file")
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
    s.add_argument("--method", help="evidence type: " + " | ".join(sorted(METHODS)))
    s.add_argument("--verdict", help="private: " + " | ".join(sorted(VERDICTS)))
    s.add_argument("--note", help="private note")
    s = sub.add_parser("claims", help="list claims and how many works support each")
    s.add_argument("-v", "--verbose", action="store_true")

    s = sub.add_parser("import-endnotes", help="load paraphrased endnote claims for a book (JSON or JSON Lines)")
    s.add_argument("book")
    s.add_argument("files", nargs="+")
    s.add_argument("--merge", action="store_true", help="update only the endnotes given; keep the rest")
    sub.add_parser("theories", help="list theories and how well each step is supported")
    s = sub.add_parser("pillars", help="the works a theory (or a whole book) rests on")
    s.add_argument("theory", nargs="?")
    s.add_argument("--book")
    s.add_argument("--top", type=int, default=20)
    s = sub.add_parser("connections", help="shared and conflicting links, rivals, candidate chains across books")
    s.add_argument("section", nargs="?", choices=["shared", "contested", "rivals", "chains", "works"])
    s.add_argument("--top", type=int, default=15)
    s = sub.add_parser("compare", help="what the books say about places, religions and social groups")
    s.add_argument("groups", nargs="*")
    s.add_argument("--top", type=int, default=12)
    s = sub.add_parser("gaps", help="thinly supported steps, one-book groups and concepts")
    s.add_argument("--top", type=int, default=15)

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
                "add-book": cmd_add_book, "refresh-book": cmd_refresh_book, "remove-book": cmd_remove_book, "merge": cmd_merge,
                "not-dup": cmd_not_dup, "tag": cmd_tag, "claims": cmd_claims, "enrich": cmd_enrich,
                "import-endnotes": cmd_import_endnotes, "theories": cmd_theories, "pillars": cmd_pillars,
                "connections": cmd_connections, "compare": cmd_compare, "gaps": cmd_gaps,
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

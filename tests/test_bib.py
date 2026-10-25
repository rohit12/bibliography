"""Tests run on a small made-up dataset, never the live data."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bib  # noqa: E402

BOOK_A = {"book": {"id": "alpha", "short": "Alpha", "title": "Alpha Book", "author": "A. Author", "year": 2020,
                   "chapters": {"Ch 1": "One", "Ch 2": "Two"}},
          "references": [
              {"authors": "Smith, John", "year": 2001, "title": "Kinship and trust", "cited_in": ["Ch 1 n1", "Ch 2 n1"]},
              {"authors": "Lee, Ann and Bob Smith", "year": 2005, "title": "Markets and fairness", "cited_in": ["Ch 1 n2"]},
              {"authors": "Khan, Omar", "year": 2010, "title": "Rice and clans", "cited_in": ["Ch 2 n2"]}]}
BOOK_B = {"book": {"id": "beta", "short": "Beta", "title": "Beta Book", "author": "B. Author", "year": 2021,
                   "chapters": {"Ch 1": "One"}},
          "references": [
              {"authors": "Smith, John", "year": 2001, "title": "Kinship and Trust", "cited_in": ["Ch 1 n1"]},
              {"authors": "Wu, Li", "year": 2015, "title": "Trust and growth", "cited_in": ["Ch 1 n2"]}]}
CONCEPTS = [{"id": c, "label": c.title(), "group": "X"} for c in ("kinship", "trust", "markets", "growth", "rice")]
GROUPS = [{"id": "india", "label": "India", "kind": "place"}, {"id": "islam", "label": "Islam", "kind": "religion"}]
NOTES_A = [{"loc": "Ch 1 n1", "claim": "Strong kin ties lower trust in strangers.", "links": [["kinship", "trust", "-"]], "about": ["india"]},
           {"loc": "Ch 1 n2", "claim": "Markets raise fairness.", "links": [["markets", "trust", "+"]]},
           {"loc": "Ch 2 n1", "claim": "Kinship again.", "links": [["kinship", "trust", "-"]]}]
NOTES_B = [{"loc": "Ch 1 n1", "claim": "Kinship raises trust.", "links": [["kinship", "trust", "+"]], "about": ["islam"]},
           {"loc": "Ch 1 n2", "claim": "Trust raises growth.", "links": [["trust", "growth", "+"]]}]
THEORY = {"id": "t1", "title": "Kin to growth", "books": ["alpha"],
          "steps": [["kinship", "trust", "-"], ["markets", "trust", "+"], ["rice", "kinship", "+"]]}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "data").mkdir()
        (root / "private").mkdir()
        bib.set_root(root)
        bib.dump_json(bib.CONCEPTS_F, CONCEPTS)
        bib.dump_json(bib.GROUPS_F, GROUPS)
        bib.dump_json(bib.THEORIES_F, [THEORY])
        db = bib.DB.load()
        db.add_book(json.loads(json.dumps(BOOK_A)))
        db.save()
        db.add_book(json.loads(json.dumps(BOOK_B)))
        db.save()
        self.assertEqual(db.import_endnotes("alpha", NOTES_A), [])
        self.assertEqual(db.import_endnotes("beta", NOTES_B), [])
        db.save_endnotes()
        self.db = bib.DB.load()

    def tearDown(self):
        bib.set_root(ROOT)
        self.tmp.cleanup()


class TestImport(Fixture):
    def test_dedupe_and_counts(self):
        self.assertEqual(len(self.db.works), 4)  # Smith 2001 matched across books
        self.assertEqual(len(self.db.citations), 6)
        self.assertEqual(self.db.books_for("smith2001kinship"), ["alpha", "beta"])
        self.assertEqual(self.db.validate(), [])

    def test_endnote_validation(self):
        probs = self.db.import_endnotes("alpha", [{"loc": "Ch 1 n9", "claim": "x", "links": [["nope", "trust", "!"]],
                                                   "about": ["mars"]}])
        self.assertEqual(len(probs), 3)
        self.assertEqual(len([n for n in self.db.endnotes if n["book"] == "alpha"]), 3)  # unchanged

    def test_merge_mode_keeps_rest(self):
        self.db.import_endnotes("alpha", [{"loc": "Ch 1 n2", "claim": "Edited."}], replace=False)
        alpha = {n["loc"]: n["claim"] for n in self.db.endnotes if n["book"] == "alpha"}
        self.assertEqual(len(alpha), 3)
        self.assertEqual(alpha["Ch 1 n2"], "Edited.")


class TestAnalysis(Fixture):
    def test_pillars(self):
        p = bib.pillars(self.db, THEORY)
        self.assertEqual(p["unsupported"], [2])           # rice → kinship has no endnote
        step0 = p["steps"][0]
        self.assertEqual(step0["works"], ["smith2001kinship"])
        self.assertEqual(len(step0["against"]), 1)        # beta says the opposite
        top = p["pillars"][0]
        self.assertIn(top["id"], {"smith2001kinship", "lee2005markets"})
        self.assertTrue(all(r["critical"] for r in p["pillars"]))

    def test_author_clusters(self):
        # Lee & Smith share an author with Smith 2001: one independent source
        self.assertEqual(len(bib.author_clusters(self.db, ["smith2001kinship", "lee2005markets"])), 1)
        self.assertEqual(len(bib.author_clusters(self.db, ["smith2001kinship", "khan2010rice"])), 2)

    def test_connections(self):
        c = bib.connections(self.db)
        self.assertTrue(any(x["from"] == "kinship" and x["to"] == "trust" for x in c["contested"]))
        chain = [x["path"] for x in c["chains"]]
        self.assertIn(["markets", "trust", "growth"], chain)   # alpha + beta, stated by neither
        self.assertEqual(c["shared_works"][0]["id"], "smith2001kinship")

    def test_compare_and_gaps(self):
        g = bib.compare(self.db, ["india", "islam"])
        self.assertEqual(g["cells"]["india"]["kinship"], [["alpha", "Ch 1 n1"]])
        gaps = bib.gaps(self.db)
        self.assertTrue(any(f["step"] == ["rice", "kinship", "+"] for f in gaps["fragile_steps"]))


class TestBuildPrivacy(Fixture):
    def test_build_publishes_only_public_fields(self):
        self.db.works["smith2001kinship"]["secret_field"] = "x"
        bib.build(self.db)
        works = json.loads((bib.SITE_DATA / "works.json").read_text())
        self.assertNotIn("secret_field", works[0])
        for f in ("endnotes", "theories", "concepts", "groups", "analysis", "meta"):
            self.assertTrue((bib.SITE_DATA / f"{f}.json").exists(), f)

    def test_private_text_blocks_build(self):
        bib.dump_json(bib.NOTES_F, {"smith2001kinship": {"note": "my private thought about this"}})
        self.db.works["smith2001kinship"]["claims"] = ["my private thought about this"]
        with self.assertRaises(SystemExit):
            bib.build(self.db)
        self.assertFalse(any(bib.SITE_DATA.glob("*.json")))


class TestHelpers(unittest.TestCase):
    def test_loc_key_and_ids(self):
        self.assertLess(bib.loc_key("Ch 2 n3", ["Ch 1", "Ch 2"]), bib.loc_key("Ch 2 n10", ["Ch 1", "Ch 2"]))
        self.assertEqual(bib.make_id("de la Croix, David", 2018, "Clans, guilds", set()), "delacroix2018clans")

    def test_enrich_uses_injected_fetch(self):
        db = bib.DB([{"id": "w1", "authors": "Smith, J.", "year": "2001", "title": "Kinship and trust", "type": "article"}], [], [])
        rec = {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/x", "display_name": "Kinship and trust",
               "publication_year": 2001, "cited_by_count": 42, "fwci": 1.5, "is_retracted": False, "topics": [],
               "authorships": [{"author": {"display_name": "J. Smith"}}], "type": "article"}
        with tempfile.TemporaryDirectory() as d:
            bib.set_root(d)
            try:
                bib.enrich(db, _get=lambda path, params: {"results": [rec]})
            finally:
                bib.set_root(ROOT)
        self.assertEqual(db.works["w1"]["oa"]["cited_by_count"], 42)


if __name__ == "__main__":
    unittest.main()

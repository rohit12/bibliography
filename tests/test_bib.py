"""Run with: python -m unittest discover -s tests"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import bib  # noqa: E402


SECOND_BOOK = {
    "book": {"id": "test-2024", "short": "Test", "title": "A Test Book", "author": "T. Ester",
             "publisher": "P", "year": 2024, "chapters": {"Ch 1": "One", "Ch 2": "Two"}},
    "references": [
        # exact overlap with Galor's list
        {"authors": "Alesina, Alberto, Paola Giuliano and Nathan Nunn", "year": "2013",
         "title": "On the Origins of Gender Roles: Women and the Plough", "venue": "QJE",
         "type": "article", "cited_in": ["Ch 1 n2", "Ch 2 n7"], "doi": "10.1093/qje/qjt005"},
        # book reprinted in a different year: should still match
        {"authors": "Diamond, Jared", "year": "1999", "title": "Guns, Germs, and Steel: The Fates of Human Societies",
         "venue": "Norton", "type": "book", "cited_in": ["Ch 1 n1"]},
        # working paper vs published version: flagged for review, not silently merged
        {"authors": "Galor, Oded, and Marc Klemp", "year": "2022", "title": "Roots of Autocracy",
         "venue": "JPE", "type": "article", "cited_in": ["Ch 2 n1"]},
        # brand new
        {"authors": "Nunn, Nathan, and Nancy Qian", "year": "2011",
         "title": "The Potato's Contribution to Population and Urbanization", "venue": "QJE",
         "type": "article", "cited_in": ["Ch 2 n3"], "flags": {"Ch 2 n3": "cites ‘Nunn (2011)’"}},
    ],
}


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in ("data", "site", "private"):
            if (REPO / d).exists():
                shutil.copytree(REPO / d, self.tmp / d, ignore=shutil.ignore_patterns("data"))
        (self.tmp / "data").mkdir(exist_ok=True)
        for f in (REPO / "data").glob("*.json"):
            shutil.copy(f, self.tmp / "data" / f.name)
        (self.tmp / "private").mkdir(exist_ok=True)
        for f in (self.tmp / "private").glob("*.json"):
            f.unlink()
        bib.set_root(self.tmp)
        self.db = bib.DB.load()

    def tearDown(self):
        bib.set_root(REPO)
        shutil.rmtree(self.tmp)


class TestImport(RepoCase):
    def test_baseline(self):
        self.assertEqual(len(self.db.works), 403)
        self.assertEqual(len(self.db.citations), 481)
        self.assertEqual(self.db.validate(), [])

    def test_second_book_matching(self):
        r = self.db.add_book(json.loads(json.dumps(SECOND_BOOK)))
        matched = dict(r.matched)
        self.assertIn("alesina2013origins", matched)
        self.assertIn("diamond1997guns", matched)
        self.assertEqual(len(r.review), 1)
        self.assertEqual(r.review[0][1], "galor2018roots")
        self.assertEqual(len(r.new), 2)
        self.db.save()
        db = bib.DB.load()
        self.assertEqual(db.validate(), [])
        self.assertEqual(db.books_for("alesina2013origins"), ["galor-2022", "test-2024"])
        self.assertEqual(db.works["alesina2013origins"]["doi"], "10.1093/qje/qjt005")
        flagged = [c for c in db.citations if c["book"] == "test-2024" and c.get("flag")]
        self.assertEqual(len(flagged), 1)

    def test_dry_run_writes_nothing(self):
        self.db.add_book(json.loads(json.dumps(SECOND_BOOK)), dry_run=True)
        self.assertNotIn("test-2024", bib.DB.load().books)
        self.assertNotIn("test-2024", self.db.books)

    def test_merge_and_remove(self):
        r = self.db.add_book(json.loads(json.dumps(SECOND_BOOK)))
        dup = r.review[0][0]
        self.db.merge("galor2018roots", dup)
        self.db.save()
        db = bib.DB.load()
        self.assertNotIn(dup, db.works)
        self.assertEqual(db.books_for("galor2018roots"), ["galor-2022", "test-2024"])
        orphans = db.remove_book("test-2024")
        self.assertEqual(len(orphans), 1)  # only the potato paper was unique to it
        self.assertEqual(len(db.works), 403)
        self.assertEqual(db.validate(), [])

    def test_ids_are_stable_slugs(self):
        ids = list(self.db.works)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("ashraf2013genetic", ids)
        self.assertIn("ashraf2013out", ids)


class TestBuildPrivacy(RepoCase):
    def test_build_excludes_private(self):
        bib.dump_json(bib.NOTES_F, {"alesina2013origins": {"verdict": "read",
                                                           "note": "SECRET-NOTE: India plough split"}})
        bib.dump_json(bib.PRIVATE / "log.json", [{"works": ["alesina2013origins"],
                                                  "text": "SECRET-LOG entry", "url": "https://example.org/private/123",
                                                  "score": 50000}])
        meta = bib.build(self.db)
        self.assertEqual(meta["works"], 403)
        site_text = "".join(p.read_text(encoding="utf-8") for p in (self.tmp / "site").rglob("*") if p.is_file())
        for secret in ("SECRET-NOTE", "SECRET-LOG", "example.org/private", "verdict"):
            self.assertNotIn(secret, site_text)
        self.assertEqual(bib.leak_check(), [])

    def test_build_refuses_private_field_in_works(self):
        self.db.works["alesina2013origins"]["private_score"] = 99999
        bib.build(self.db)  # field is dropped by the allowlist, so this passes
        pub = json.loads((self.tmp / "site/data/works.json").read_text())
        self.assertTrue(all("private_score" not in w for w in pub))
        # but if something sneaks a private string into a public field, the guard trips
        bib.dump_json(bib.PRIVATE / "log.json", [{"text": "a very private piece of text"}])
        self.db.works["alesina2013origins"]["claims"] = ["a very private piece of text"]
        with self.assertRaises(SystemExit):
            bib.build(self.db)
        self.assertFalse(any((self.tmp / "site/data").glob("*")))  # nothing left behind


class TestExtensions(RepoCase):
    def test_private_ext_adds_command(self):
        (self.tmp / "private" / "ext.py").write_text(
            "def register(sub, handlers):\n"
            "    sub.add_parser('hello')\n"
            "    handlers['hello'] = lambda db, a: print('hello', len(db.works)) or 7\n")
        self.assertEqual(bib.main(["hello"]), 7)
        self.assertEqual(bib.main(["validate"]), 0)


class TestEnrichAndExport(RepoCase):
    def test_enrich_with_fake_openalex(self):
        def fake_get(path, params):
            if "search" in params and "plough" in params["search"].lower():
                return {"results": [
                    {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1093/qje/qjt005",
                     "display_name": "On the Origins of Gender Roles: Women and the Plough",
                     "publication_year": 2013, "cited_by_count": 2500, "fwci": 30.1, "is_retracted": False,
                     "topics": [{"display_name": "Gender and Labor"}],
                     "authorships": [{"author": {"display_name": "Alberto Alesina"}}]}]}
            if "search" in params and "Fractionalization" in params["search"]:
                return {"results": [  # wrong paper: right title words, wrong author
                    {"id": "https://openalex.org/W2", "display_name": "Fractionalization",
                     "publication_year": 2003, "authorships": [{"author": {"display_name": "Someone Else"}}]}]}
            return {"results": []}
        for wid in list(self.db.works):
            if wid not in ("alesina2013origins", "alesina2003fractionalization"):
                self.db.works[wid]["oa"] = {"id": "skip"}
        m, un, ret = bib.enrich(self.db, _get=fake_get)
        self.assertEqual(m, 1)
        self.assertEqual(un, ["alesina2003fractionalization"])
        w = bib.DB.load().works["alesina2013origins"]
        self.assertEqual(w["doi"], "10.1093/qje/qjt005")
        self.assertEqual(w["oa"]["cited_by_count"], 2500)

    def test_exports(self):
        works = sorted(self.db.works.values(), key=lambda w: w["id"])
        bt = bib.to_bibtex(works)
        self.assertEqual(bt.count("\n@"), 402)
        self.assertIn("@article{alesina2013origins,", bt)
        self.assertIn("Alesina, Alberto and Giuliano, Paola and Nunn, Nathan", bt)
        csl = bib.to_csl(works)
        self.assertEqual(len(csl), 403)
        self.assertTrue(bib.to_csv(self.db, works).startswith("id,authors"))


class TestHelpers(unittest.TestCase):
    def test_parse_authors(self):
        a = bib.parse_authors("Abramovitz, Moses, and Paul A. David")
        self.assertEqual(a, [{"family": "Abramovitz", "given": "Moses"}, {"family": "David", "given": "Paul A."}])
        self.assertEqual(bib.parse_authors("OECD"), [{"literal": "OECD"}])
        self.assertEqual(bib.parse_authors("Bennett, Matthew R. et al.")[-1], {"literal": "others"})

    def test_surname_key(self):
        self.assertEqual(bib.surname_key("de la Croix, David"), "delacroix")
        self.assertEqual(bib.surname_key("Özak, Ömer"), "ozak")


if __name__ == "__main__":
    unittest.main()

"""Regression tests for refaudit: dedupe, DOI verification, claim support.

Run: python -m unittest discover -s tests -v
No network: every lookup is replaced by a fixture.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import refaudit as ra  # noqa: E402
import cslformat as cf  # noqa: E402
import tagged as tg  # noqa: E402
import export as ex  # noqa: E402
import suggest as sg  # noqa: E402


def rec(rid, title, doi="", year="2023", author="Zhang, San", **fields):
    f = {"title": title, "year": year, "author": author}
    if doi:
        f["doi"] = doi
    f.update(fields)
    return {"id": rid, "type": fields.pop("type", "article"), "fields": f, "persons": {}}


class FoldTests(unittest.TestCase):
    def test_han_survives_folding(self):
        self.assertEqual(ra.fold("钙钛矿太阳能电池"), "钙钛矿太阳能电池")

    def test_distinct_chinese_titles_stay_distinct(self):
        a = ra.fold("钙钛矿太阳能电池界面钝化研究")
        b = ra.fold("二维材料拉曼光谱的温度依赖")
        self.assertLess(ra.ratio(a, b), 0.5)

    def test_latex_and_entities(self):
        self.assertEqual(ra.fold(r"\emph{Deep} Learning \& Fren&#x00E9;t"), "deeplearningfrenét")

    def test_doi_normalisation(self):
        self.assertEqual(ra.norm_doi("https://doi.org/10.1038/NATURE14539."), "10.1038/nature14539")
        self.assertEqual(ra.norm_doi(r"10.1000/a\_b"), "10.1000/a_b")
        self.assertEqual(ra.norm_doi("not a doi"), "")


class DedupeTests(unittest.TestCase):
    def test_identical_doi_merges_and_keeps_richer_record(self):
        entries = [
            rec("a", "Deep learning", "10.1038/nature14539", journal="Nature"),
            rec("b", "Deep learning", "10.1038/nature14539", journal="Nature", volume="521", pages="436-444"),
        ]
        out = ra.dedupe(entries)
        self.assertEqual(len(out["groups"]), 1)
        g = out["groups"][0]
        self.assertEqual(set(g["members"]), {"a", "b"})
        self.assertEqual(g["canonical"], "b")
        self.assertIn("doi_exact", g["reasons"])

    def test_identical_doi_with_conflicting_titles_needs_confirmation(self):
        entries = [
            rec("a", "Deep learning", "10.1038/nature14539"),
            rec("b", "Raman spectra of two-dimensional materials", "10.1038/nature14539"),
        ]
        out = ra.dedupe(entries)
        self.assertEqual(len(out["groups"]), 1)
        self.assertTrue(out["groups"][0]["confirmation_required"])

    def test_preprint_and_published_merge(self):
        entries = [
            rec("pre", "Machine learning potentials for halide perovskites",
                "10.48550/arXiv.2201.00001", year="2022", author="Li, Na"),
            rec("pub", "Machine learning potentials for halide perovskites",
                "10.1000/demo.2022.777", year="2022", author="Li, Na",
                journal="Computational Materials Today", volume="15", pages="100321"),
        ]
        out = ra.dedupe(entries)
        self.assertEqual(len(out["groups"]), 1)
        g = out["groups"][0]
        self.assertEqual(g["canonical"], "pub")
        self.assertIn("preprint_published", g["reasons"])

    def test_chinese_titles_with_punctuation_difference_merge(self):
        entries = [
            rec("c1", "钙钛矿太阳能电池界面钝化研究", "10.1000/a", year="2023", author="Zhang, San"),
            rec("c2", "钙钛矿太阳能电池的界面钝化研究", journal="物理学报", year="2023", author="Zhang, San"),
        ]
        out = ra.dedupe(entries)
        self.assertEqual(len(out["groups"]), 1, out)

    def test_different_dois_are_not_auto_merged(self):
        entries = [
            rec("x", "Deep learning", "10.1038/nature14539", journal="Nature"),
            rec("y", "Deep learning", "10.1000/other.1", journal="Other"),
        ]
        out = ra.dedupe(entries)
        self.assertEqual(out["groups"], [])
        self.assertEqual(len(out["conflicts"]), 1)
        self.assertEqual(out["conflicts"][0]["reason"], "doi_conflict")

    def test_unrelated_chinese_titles_do_not_merge(self):
        entries = [
            rec("m", "钙钛矿太阳能电池界面钝化研究", year="2023", author="Zhang, San"),
            rec("n", "二维材料拉曼光谱的温度依赖", year="2023", author="Zhang, San"),
        ]
        out = ra.dedupe(entries)
        self.assertEqual(out["groups"], [])


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "cache.json"

    def _run(self, entries, crossref=None, openalex=None, datacite=None, handle=(False, "ok")):
        if openalex is None:
            openalex = (None, "not_found")
        with mock.patch.object(ra, "crossref_record", return_value=crossref), \
             mock.patch.object(ra, "openalex_record", return_value=openalex), \
             mock.patch.object(ra, "datacite_record", return_value=datacite), \
             mock.patch.object(ra, "handle_exists", return_value=handle):
            return ra.verify(entries, online=True, cache_path=self.tmp, mailto="")

    def test_openalex_resolves_when_crossref_misses(self):
        found = ({"title": "Deep learning", "year": "2015", "authors": ["Yann LeCun"],
                  "container": "Nature", "publisher": "", "type": "article"}, "ok")
        out = self._run([rec("a", "Deep learning", "10.1038/nature14539", year="2015",
                             author="LeCun, Yann")],
                        crossref=(None, "not_found"), openalex=found)
        item = out["records"][0]
        self.assertEqual(item["verdict"], "VERIFIED")
        self.assertEqual(item["source"], "openalex")

    def test_verified(self):
        found = ({"title": "Deep learning", "year": "2015", "authors": ["LeCun, Yann"],
                  "container": "Nature", "publisher": "", "type": "journal-article"}, "ok")
        out = self._run([rec("a", "Deep learning", "10.1038/nature14539", year="2015",
                             author="LeCun, Yann")], crossref=found)
        self.assertEqual(out["records"][0]["verdict"], "VERIFIED")

    def test_year_mismatch_is_partial_not_fabrication(self):
        found = ({"title": "Deep learning", "year": "2016", "authors": ["LeCun, Yann"],
                  "container": "Nature", "publisher": "", "type": ""}, "ok")
        out = self._run([rec("a", "Deep learning", "10.1038/nature14539", year="2015",
                             author="LeCun, Yann")], crossref=found)
        self.assertEqual(out["records"][0]["verdict"], "PARTIAL")

    def test_doi_pointing_elsewhere_is_mismatch(self):
        found = ({"title": "A study of something unrelated", "year": "2016",
                  "authors": ["Nobody"], "container": "", "publisher": "", "type": ""}, "ok")
        out = self._run([rec("a", "Deep learning", "10.1038/nature14539")], crossref=found)
        self.assertEqual(out["records"][0]["verdict"], "DOI_MISMATCH")

    def test_unregistered_doi_is_unresolved(self):
        out = self._run([rec("a", "Deep learning", "10.9999/does.not.exist")],
                        crossref=(None, "not_found"), datacite=(None, "not_found"),
                        handle=(False, "ok"))
        self.assertEqual(out["records"][0]["verdict"], "UNRESOLVED")

    def test_transport_failure_is_inconclusive(self):
        out = self._run([rec("a", "Deep learning", "10.1038/nature14539")],
                        crossref=(None, "transport:URLError"), datacite=(None, "transport:URLError"))
        self.assertEqual(out["records"][0]["verdict"], "INCONCLUSIVE")

    def test_missing_doi_is_unverifiable(self):
        out = self._run([rec("a", "Deep learning", "")])
        self.assertEqual(out["records"][0]["verdict"], "UNVERIFIABLE")

    def test_crossref_miss_with_dead_registries_is_scoped_unresolved(self):
        out = self._run([rec("a", "Deep learning", "10.1038/nature99999")],
                        crossref=(None, "not_found"), datacite=(None, "transport:URLError"),
                        handle=(False, "transport:URLError"))
        item = out["records"][0]
        self.assertEqual(item["verdict"], "UNRESOLVED")
        self.assertIn("crossref only", item["scope"])

    def test_datacite_prefix_stays_inconclusive_when_datacite_is_down(self):
        out = self._run([rec("a", "Attention is all you need", "10.48550/arXiv.1706.03762")],
                        crossref=(None, "not_found"), datacite=(None, "transport:URLError"),
                        handle=(False, "transport:URLError"))
        self.assertEqual(out["records"][0]["verdict"], "INCONCLUSIVE")


class SupportTests(unittest.TestCase):
    ABSTRACT = ("Perovskite solar cells have reached certified efficiencies above 26%. "
                "Interfacial passivation reduces non-radiative recombination losses. "
                "Long-term stability remains limited by ion migration.")

    def test_supported_claim(self):
        out = ra.support([{"claim_id": "c1",
                           "text": "Interfacial passivation reduces non-radiative recombination losses.",
                           "abstract": self.ABSTRACT}])
        self.assertEqual(out["claims"][0]["verdict"], "SUPPORTED")
        self.assertTrue(out["claims"][0]["evidence"])

    def test_number_not_in_abstract_is_partial(self):
        out = ra.support([{"claim_id": "c2",
                           "text": "Certified efficiencies above 33% were reported for perovskite cells.",
                           "abstract": self.ABSTRACT}])
        self.assertEqual(out["claims"][0]["verdict"], "PARTIAL")
        self.assertTrue(any("33" in n for n in out["claims"][0]["notes"]))

    def test_unrelated_claim_is_not_supported(self):
        out = ra.support([{"claim_id": "c3",
                           "text": "Raman spectra of two-dimensional materials depend on temperature.",
                           "abstract": self.ABSTRACT}])
        self.assertEqual(out["claims"][0]["verdict"], "NOT_SUPPORTED")

    def test_missing_material_is_unverifiable(self):
        out = ra.support([{"claim_id": "c4", "text": "Something is claimed."}])
        self.assertEqual(out["claims"][0]["verdict"], "UNVERIFIABLE")


class CliTests(unittest.TestCase):
    def test_dedupe_cli_roundtrip(self):
        tmp = Path(tempfile.mkdtemp())
        bib = tmp / "bib.json"
        bib.write_text(json.dumps({"entries": [
            rec("a", "Deep learning", "10.1038/nature14539"),
            rec("b", "Deep learning", "10.1038/nature14539"),
        ]}), encoding="utf-8")
        out = tmp / "merges.json"
        with mock.patch.object(sys, "argv", ["refaudit.py", "dedupe", str(bib), str(out)]):
            self.assertEqual(ra.main(), 0)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["merged_total"], 1)


class FormatTests(unittest.TestCase):
    LECUN = {
        "id": "lecun2015", "type": "article",
        "fields": {"title": "Deep learning", "journal": "Nature", "year": "2015",
                   "volume": "521", "number": "7553", "pages": "436--444",
                   "doi": "10.1038/nature14539"},
        "persons": {"author": ["LeCun, Yann", "Bengio, Yoshua", "Hinton, Geoffrey"]},
    }

    def test_gbt_english_article(self):
        self.assertEqual(
            cf.fmt_gbt(self.LECUN),
            "LECUN Y, BENGIO Y, HINTON G. Deep learning[J]. Nature, 2015, 521(7553): 436-444. DOI: 10.1038/nature14539.")

    def test_gbt_more_than_three_authors_uses_et_al(self):
        rec_ = {"id": "s", "type": "article",
                "fields": {"title": "Encapsulation strategies", "journal": "Demo Energy",
                           "year": "2020", "volume": "4", "number": "2", "pages": "88--101"},
                "persons": {"author": ["Sun, Li", "Xu, Bo", "Ma, Chao", "Gao, Di"]}}
        out = cf.fmt_gbt(rec_)
        self.assertTrue(out.startswith("SUN L, XU B, MA C, et al."), out)

    def test_gbt_chinese_article(self):
        rec_ = {"id": "z", "type": "article",
                "fields": {"title": "钙钛矿太阳能电池界面钝化研究进展", "journal": "物理学报",
                           "year": "2023", "volume": "72", "number": "14", "pages": "148701"},
                "persons": {"author": ["张三", "李四"]}}
        self.assertEqual(
            cf.fmt_gbt(rec_),
            "张三, 李四. 钙钛矿太阳能电池界面钝化研究进展[J]. 物理学报, 2023, 72(14): 148701.")

    def test_gbt_arxiv_preprint_is_eb_ol(self):
        rec_ = {"id": "v", "type": "misc",
                "fields": {"title": "Attention is all you need", "year": "2017",
                           "doi": "10.48550/arXiv.1706.03762"},
                "persons": {"author": ["Vaswani, Ashish"]}}
        out = cf.fmt_gbt(rec_)
        self.assertIn("[EB/OL]", out)
        self.assertIn("https://arxiv.org/abs/1706.03762", out)
        self.assertIn("(2017)[", out)

    def test_apa_english_article(self):
        self.assertEqual(
            cf.fmt_apa(self.LECUN),
            "LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. Nature, 521(7553), 436-444. https://doi.org/10.1038/nature14539")


class TaggedTests(unittest.TestCase):
    def _parse(self, text: str, encoding: str = "utf-8") -> list[dict]:
        tmp = Path(tempfile.mkdtemp()) / "refs.txt"
        tmp.write_bytes(text.encode(encoding, "replace"))
        family = tg.detect_family([ln for ln in text.splitlines() if ln.strip()])
        return tg.finalize(tg.parse_records(text, family))

    def test_ris(self):
        text = ("TY  - JOUR\nAU  - LeCun, Yann\nAU  - Bengio, Yoshua\n"
                "TI  - Deep learning\nJO  - Nature\nPY  - 2015\nVL  - 521\n"
                "IS  - 7553\nSP  - 436\nEP  - 444\nDO  - 10.1038/nature14539\nER  - \n")
        recs = self._parse(text)
        self.assertEqual(len(recs), 1)
        f = recs[0]["fields"]
        self.assertEqual(f["pages"], "436-444")
        self.assertEqual(f["doi"], "10.1038/nature14539")
        self.assertEqual(recs[0]["persons"]["author"], ["LeCun, Yann", "Bengio, Yoshua"])

    def test_cnki_endnote_tagged_gbk(self):
        text = ("%0 Journal Article\n%A 张三\n%A 李四\n"
                "%T 钙钛矿太阳能电池界面钝化研究进展\n%J 物理学报\n%D 2023\n%V 72\n%N 14\n%P 148701\n")
        raw = text.encode("gb18030")
        decoded, enc = tg.decode(raw)
        self.assertEqual(enc, "gb18030")
        family = tg.detect_family([ln for ln in decoded.splitlines() if ln.strip()])
        self.assertEqual(family, "endnote")
        recs = tg.finalize(tg.parse_records(decoded, family))
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["fields"]["title"], "钙钛矿太阳能电池界面钝化研究进展")
        self.assertEqual(recs[0]["fields"]["journal"], "物理学报")

    def test_refworks(self):
        text = ("RT Journal Article\nA1 Author, Alice\nA1 Author, Bob\n"
                "T1 Remote Sensing Revisited\nJF Demo Letters\nYR 2026\nVO 4\nIS 2\nSP 88-101\n")
        recs = self._parse(text)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["fields"]["pages"], "88-101")

    def test_generated_ids_are_unique(self):
        text = ("TY  - JOUR\nAU  - Smith, John\nTI  - A study\nPY  - 2020\nER  - \n"
                "TY  - JOUR\nAU  - Smith, John\nTI  - A study\nPY  - 2020\nER  - \n")
        recs = self._parse(text)
        self.assertNotEqual(recs[0]["id"], recs[1]["id"])


class MarkerAuditTests(unittest.TestCase):
    NUMBERING = {
        "clusters": [{"id": "c1", "numbers": [1]}, {"id": "c2", "numbers": [2, 3]},
                     {"id": "c3", "numbers": [1]}],
        "bibliography": [{"id": "b", "number": 1}, {"id": "a", "number": 2},
                         {"id": "c", "number": 3}],
    }

    def test_consistent_manuscript_passes(self):
        ms = ("正文第一段[1]。第二段需要两条文献[2,3]。再次引用第一条[1]。\n\n"
              "## 参考文献\n\n[1] demoB entry\n[2] demoA entry\n[3] demoC entry\n")
        out = ra.audit_markers(ms, self.NUMBERING)
        self.assertTrue(out["ok"], out)

    def test_swapped_body_markers_fail(self):
        ms = ("正文第一段[1]。第二段[3,2]。再次引用[1]。\n\n"
              "## 参考文献\n\n[1] demoB entry\n[2] demoA entry\n[3] demoC entry\n")
        out = ra.audit_markers(ms, self.NUMBERING)
        self.assertFalse(out["ok"])
        self.assertEqual(out["discrepancies"][0]["kind"], "body_cluster")

    def test_bibliography_order_fail(self):
        ms = ("正文第一段[1]。第二段[2,3]。再次引用[1]。\n\n"
              "## 参考文献\n\n[2] demoA entry\n[1] demoB entry\n[3] demoC entry\n")
        out = ra.audit_markers(ms, self.NUMBERING)
        self.assertFalse(out["ok"])
        kinds = {d["kind"] for d in out["discrepancies"]}
        self.assertIn("bibliography", kinds)


class ExportTests(unittest.TestCase):
    ENTRY = {
        "id": "lecun2015", "type": "article",
        "fields": {"title": "Deep learning", "journal": "Nature", "year": "2015",
                   "volume": "521", "number": "7553", "pages": "436-444",
                   "doi": "10.1038/nature14539"},
        "persons": {"author": ["LeCun, Yann", "Bengio, Yoshua", "Hinton, Geoffrey"]},
    }

    def test_ris_shape(self):
        lines = ex.to_ris(self.ENTRY)
        self.assertEqual(lines[0], "TY  - JOUR")
        self.assertIn("AU  - LeCun, Yann", lines)
        self.assertIn("SP  - 436", lines)
        self.assertIn("EP  - 444", lines)
        self.assertEqual(lines[-1], "ER  - ")

    def test_bib_shape(self):
        lines = ex.to_bib(self.ENTRY)
        self.assertTrue(lines[0].startswith("@article{lecun2015,"))
        text = "\n".join(lines)
        self.assertIn("doi     = {10.1038/nature14539}", text)
        self.assertIn("pages   = {436--444}", text)

    def test_ris_roundtrip_through_tagged(self):
        ris = "\n".join(ex.to_ris(self.ENTRY))
        family = tg.detect_family([ln for ln in ris.splitlines() if ln.strip()])
        recs = tg.finalize(tg.parse_records(ris, family))
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["fields"]["doi"], "10.1038/nature14539")
        self.assertEqual(recs[0]["fields"]["pages"], "436-444")


class SuggestTests(unittest.TestCase):
    LIB = [
        {"id": "zhang2023", "fields": {"title": "钙钛矿太阳能电池界面钝化研究进展"}},
        {"id": "li2022", "fields": {"title": "Machine learning potentials for molecular dynamics of halide perovskites"}},
    ]

    def test_uncited_matching_sentence_gets_candidate(self):
        sent = "界面钝化能显著降低非辐射复合损失"
        ranked = sorted(((sg.score(sent, r), r["id"]) for r in self.LIB), reverse=True)
        self.assertEqual(ranked[0][1], "zhang2023")
        self.assertGreater(ranked[0][0], 0)

    def test_already_cited_sentence_is_skipped(self):
        text = "界面钝化能显著降低非辐射复合损失[D1]。\n"
        self.assertRegex(text, sg.CITE_RE)

    def test_unrelated_sentence_scores_low(self):
        sent = "拉曼光谱随温度发生位移"
        best = max(sg.score(sent, r) for r in self.LIB)
        self.assertLess(best, 0.3)


if __name__ == "__main__":
    unittest.main()

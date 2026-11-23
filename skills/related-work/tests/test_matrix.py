"""Tests for matrix.py: thin records, group defects, coverage and overclaim scan."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import matrix  # noqa: E402


def src(**kw):
    base = {"id": "s1", "problem": "p", "method": "m", "result": "r",
            "condition": "c", "limitation": "l", "anchor": "paper.pdf p2"}
    base.update(kw)
    return base


class MatrixTests(unittest.TestCase):
    def test_thin_record(self):
        out = matrix.check([src(limitation="")], [{"name": "G1", "basis": "同一测试集", "sources": ["s1"]}], "[s1]")
        self.assertIn("thin_record", [i["kind"] for i in out["issues"]])

    def test_single_source_group(self):
        out = matrix.check([src()], [{"name": "G1", "basis": "同一指标", "sources": ["s1"]}], "[s1]")
        self.assertIn("group_defect", [i["kind"] for i in out["issues"]])

    def test_missing_basis(self):
        out = matrix.check([src(id="a"), src(id="b")],
                           [{"name": "G1", "basis": "", "sources": ["a", "b"]}], "[a] [b]")
        kinds = [i for i in out["issues"] if i["kind"] == "group_defect"]
        self.assertTrue(any("维度" in i["detail"] or "口径" in i["detail"] for i in kinds))

    def test_uncited_source(self):
        out = matrix.check([src(id="a"), src(id="b")],
                           [{"name": "G1", "basis": "同一指标", "sources": ["a", "b"]}], "只有 [a] 被引用")
        self.assertIn("uncited_source", [i["kind"] for i in out["issues"]])

    def test_overclaim_flagged(self):
        out = matrix.check([src(id="a"), src(id="b")],
                           [{"name": "G1", "basis": "同一指标", "sources": ["a", "b"]}],
                           "[a][b] 本文首次提出跨地区统一的遥感模型。")
        self.assertIn("overclaim", [i["kind"] for i in out["issues"]])

    def test_clean_matrix_passes(self):
        out = matrix.check([src(id="a"), src(id="b")],
                           [{"name": "G1", "basis": "同一测试集与指标", "sources": ["a", "b"]}],
                           "已有工作 [a] 与 [b] 在同一测试集上比较，未见跨地区验证。")
        self.assertEqual(out["issues"], [])


if __name__ == "__main__":
    unittest.main()

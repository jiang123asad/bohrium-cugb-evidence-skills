"""Tests for terms.py: abbreviation, variant, casing and hyphen consistency."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import terms  # noqa: E402


GLOSSARY = [{
    "canonical": "perovskite solar cell",
    "variants": ["perovskite photovoltaic"],
    "abbr": "PSC",
    "definition": "perovskite solar cell (PSC)",
}]


class AbbreviationTests(unittest.TestCase):
    def test_abbr_before_definition(self):
        text = "PSC efficiency rose sharply. Later we study the perovskite solar cell (PSC) in detail."
        out = terms.scan(text, GLOSSARY)
        kinds = [i["kind"] for i in out["issues"]]
        self.assertIn("abbr_before_definition", kinds)

    def test_defined_before_use_is_clean(self):
        text = "We study the perovskite solar cell (PSC). PSC efficiency rose."
        out = terms.scan(text, GLOSSARY)
        self.assertNotIn("abbr_before_definition", [i["kind"] for i in out["issues"]])

    def test_undefined_abbr_reported(self):
        text = "The XPS spectra were fitted. XPS confirms the oxidation state."
        out = terms.scan(text, [])
        self.assertIn("undefined_abbr", [i["kind"] for i in out["issues"]])

    def test_protected_abbr_not_reported(self):
        text = "DNA and RNA were extracted. DNA yield was high."
        out = terms.scan(text, [])
        self.assertNotIn("undefined_abbr", [i["kind"] for i in out["issues"]])


class VariantTests(unittest.TestCase):
    def test_variant_mixed(self):
        text = ("Perovskite solar cells are stable. The perovskite photovoltaic device also passed. "
                "Another perovskite solar cell was tested.")
        out = terms.scan(text, GLOSSARY)
        self.assertIn("variant_mixed", [i["kind"] for i in out["issues"]])

    def test_casing_inconsistency(self):
        text = "Machine Learning models were trained. machine learning models generalize well."
        out = terms.scan(text, [])
        self.assertIn("inconsistent_casing", [i["kind"] for i in out["issues"]])

    def test_hyphen_variant(self):
        text = "We used a co-author workflow. Later the coauthor workflow was automated."
        out = terms.scan(text, [])
        self.assertIn("hyphen_variant", [i["kind"] for i in out["issues"]])

    def test_clean_text_has_no_issues(self):
        text = ("We study the perovskite solar cell (PSC). PSC efficiency rose. "
                "The same perovskite solar cell was stable.")
        out = terms.scan(text, GLOSSARY)
        self.assertEqual(out["issues"], [])


class CliTests(unittest.TestCase):
    def test_cli_writes_json(self):
        tmp = Path(tempfile.mkdtemp())
        t = tmp / "text.md"
        t.write_text("PSC rose. The perovskite solar cell (PSC) was stable.", encoding="utf-8")
        out = tmp / "terms.json"
        old = sys.argv
        sys.argv = ["terms.py", str(t), str(out)]
        try:
            self.assertEqual(terms.main(), 0)
        finally:
            sys.argv = old
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("issues", data)


if __name__ == "__main__":
    unittest.main()

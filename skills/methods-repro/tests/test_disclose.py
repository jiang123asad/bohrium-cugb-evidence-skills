"""Tests for disclose.py: parameter disclosure and text/facts agreement."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import disclose  # noqa: E402


def fact(run="r1", parameter="lr", value="0.01", unit="", file="train.log", locator="L12"):
    return {"run": run, "parameter": parameter, "value": value, "unit": unit,
            "file": file, "locator": locator}


class DiscloseTests(unittest.TestCase):
    def test_unit_missing_in_facts(self):
        out = disclose.check([fact(parameter="batch", value="64")], "batch size was 64", [])
        self.assertIn("unit_missing_in_facts", [i["kind"] for i in out["issues"]])

    def test_dimensionless_parameter_needs_no_unit(self):
        out = disclose.check([fact(value="0.01")], "lr was 0.01", [])
        self.assertNotIn("unit_missing_in_facts", [i["kind"] for i in out["issues"]])

    def test_multi_run_values(self):
        facts = [fact(value="0.01"), fact(value="0.005")]
        out = disclose.check(facts, "lr = 0.01", [])
        self.assertIn("multi_run_values", [i["kind"] for i in out["issues"]])

    def test_parameter_absent_from_text(self):
        out = disclose.check([fact()], "we trained a model", [])
        self.assertIn("parameter_not_in_text", [i["kind"] for i in out["issues"]])

    def test_value_absent_from_text(self):
        out = disclose.check([fact()], "learning rate lr was tuned", [])
        self.assertIn("value_not_in_text", [i["kind"] for i in out["issues"]])

    def test_unit_missing_in_text(self):
        facts = [fact(parameter="batch", value="64", unit="samples")]
        out = disclose.check(facts, "we used batch size 64 for all runs", [])
        self.assertIn("unit_missing_in_text", [i["kind"] for i in out["issues"]])

    def test_clean_disclosure(self):
        facts = [fact(parameter="batch", value="64", unit="samples")]
        out = disclose.check(facts, "We used batch size 64 samples, seed 42 was fixed.", [])
        self.assertNotIn("unit_missing_in_text", [i["kind"] for i in out["issues"]])


if __name__ == "__main__":
    unittest.main()

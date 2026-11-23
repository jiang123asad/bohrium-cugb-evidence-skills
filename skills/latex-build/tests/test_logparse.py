"""Tests for scripts/logparse.py using small synthetic LaTeX logs."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.logparse import parse_text  # noqa: E402

LOG_UNDEFINED_REF = """\
This is XeTeX, Version 3.141592653-2.6-0.999993 (TeX Live 2021)
(./main.tex
LaTeX2e <2021-11-15>
(./sections/intro.tex
LaTeX Warning: Reference `fig:map' on page 3 undefined on input line 12.

)
)
"""

LOG_UNDEFINED_CITATION = """\
(./main.tex
LaTeX Warning: Citation `smith2020' on page 2 undefined on input line 34.
)
"""

LOG_LATEX_ERROR = """\
(./main.tex
(./sections/method.tex
! LaTeX Error: Environment equation* undefined.
l.42 \\begin{equation*}
"""

LOG_MISSING_FILE = """\
(./main.tex
! LaTeX Error: File `figures/flow.pdf' not found.

Type X to quit or <RETURN> to proceed,
or enter new name. (Default extension: sty)

Enter file name:
l.7 \\includegraphics{figures/flow}
"""

LOG_OVERFULL = """\
(./main.tex
Overfull \\hbox (12.34pt too wide) in paragraph at lines 21--23
"""

LOG_UNDERFULL = """\
(./main.tex
Underfull \\vbox (badness 10000) has occurred while \\output is active [3]
"""

LOG_CLEAN = """\
This is pdfTeX, Version 3.141592653-2.6-1.40.24 (TeX Live 2022)
(./main.tex
LaTeX Font Info:    Checking defaults for OML/cmm/m/it on input line 2.
[1] (./main.aux) )
Output written on main.pdf (1 page, 5000 bytes).
"""

LOG_FILE_LINE = """\
(./chapters/results.tex
./chapters/results.tex:17: Undefined control sequence.
l.17 \\badmacro
"""

LOG_MIXED = """\
(./main.tex
Overfull \\hbox (3.0pt too wide) detected at line 5
LaTeX Warning: Reference `sec:x' on page 1 undefined on input line 9.
! Undefined control sequence.
l.12 \\foo
"""


def only(report):
    """Return the single diagnostic of a report."""
    diagnostics = report["diagnostics"]
    if len(diagnostics) != 1:
        raise AssertionError(f"expected 1 diagnostic, got {len(diagnostics)}: {diagnostics}")
    return diagnostics[0]


class LogParseTests(unittest.TestCase):
    def test_undefined_reference_has_file_and_line(self):
        report = parse_text(LOG_UNDEFINED_REF)
        diagnostic = only(report)
        self.assertEqual(diagnostic["severity"], "warning")
        self.assertEqual(diagnostic["kind"], "undefined_reference")
        self.assertEqual(diagnostic["file"], "sections/intro.tex")
        self.assertEqual(diagnostic["line"], 12)
        self.assertIn("fig:map", diagnostic["message"])
        self.assertEqual(report["summary"], {"error": 0, "warning": 1, "box": 0})

    def test_undefined_citation(self):
        diagnostic = only(parse_text(LOG_UNDEFINED_CITATION))
        self.assertEqual(diagnostic["severity"], "warning")
        self.assertEqual(diagnostic["kind"], "undefined_citation")
        self.assertEqual(diagnostic["file"], "main.tex")
        self.assertEqual(diagnostic["line"], 34)
        self.assertIn("smith2020", diagnostic["message"])

    def test_latex_error_block_with_l_marker_and_file_context(self):
        diagnostic = only(parse_text(LOG_LATEX_ERROR))
        self.assertEqual(diagnostic["severity"], "error")
        self.assertEqual(diagnostic["kind"], "latex_error")
        self.assertEqual(diagnostic["file"], "sections/method.tex")
        self.assertEqual(diagnostic["line"], 42)
        self.assertIn("l.42", diagnostic["context"])
        self.assertIn("Environment equation* undefined", diagnostic["message"])

    def test_missing_file_error_keeps_line_across_blank_lines(self):
        diagnostic = only(parse_text(LOG_MISSING_FILE))
        self.assertEqual(diagnostic["severity"], "error")
        self.assertEqual(diagnostic["kind"], "missing_file")
        self.assertEqual(diagnostic["file"], "main.tex")
        self.assertEqual(diagnostic["line"], 7)
        self.assertIn("figures/flow.pdf", diagnostic["message"])

    def test_overfull_hbox_line_range(self):
        diagnostic = only(parse_text(LOG_OVERFULL))
        self.assertEqual(diagnostic["severity"], "box")
        self.assertEqual(diagnostic["kind"], "overfull_hbox")
        self.assertEqual(diagnostic["file"], "main.tex")
        self.assertEqual(diagnostic["line"], 21)
        self.assertEqual(diagnostic["line_end"], 23)

    def test_underfull_vbox_without_line(self):
        diagnostic = only(parse_text(LOG_UNDERFULL))
        self.assertEqual(diagnostic["severity"], "box")
        self.assertEqual(diagnostic["kind"], "underfull_vbox")
        self.assertIsNone(diagnostic["line"])
        self.assertIsNone(diagnostic["line_end"])

    def test_clean_log_is_reported_explicitly(self):
        report = parse_text(LOG_CLEAN)
        self.assertEqual(report["diagnostics"], [])
        self.assertEqual(report["summary"], {"error": 0, "warning": 0, "box": 0})
        self.assertEqual(report["status"], "clean")
        self.assertIn("no diagnostics", report["next_action"])

    def test_file_line_error_form(self):
        diagnostic = only(parse_text(LOG_FILE_LINE))
        self.assertEqual(diagnostic["severity"], "error")
        self.assertEqual(diagnostic["kind"], "undefined_control_sequence")
        self.assertEqual(diagnostic["file"], "chapters/results.tex")
        self.assertEqual(diagnostic["line"], 17)

    def test_queue_is_ordered_errors_then_warnings_then_boxes(self):
        report = parse_text(LOG_MIXED)
        self.assertEqual([d["severity"] for d in report["diagnostics"]],
                         ["error", "warning", "box"])
        self.assertEqual(report["summary"], {"error": 1, "warning": 1, "box": 1})
        self.assertTrue(report["next_action"].startswith(
            "fix undefined control sequence at main.tex:12 before rebuilding"))

    def test_cli_writes_json_report(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            workdir = Path(tmp)
            log = workdir / "synthetic-build.txt"
            out = workdir / "log-queue.json"
            log.write_text(LOG_UNDEFINED_REF, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "logparse.py"),
                 str(log), str(out)],
                capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out.is_file())
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["summary"], {"error": 0, "warning": 1, "box": 0})
            self.assertEqual(report["diagnostics"][0]["file"], "sections/intro.tex")
            self.assertIn("logparse:", proc.stdout)


if __name__ == "__main__":
    unittest.main()

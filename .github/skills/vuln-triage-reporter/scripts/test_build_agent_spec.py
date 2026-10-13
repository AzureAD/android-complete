# Copyright (c) Microsoft Corporation. All rights reserved.
"""Synthetic CLI regression tests for agent-spec approval and evidence gates."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPORT = """# Synthetic finding
**Component:** common
**Disposition:** Keep
**Confidence:** High
**External validation:** No
**Risk decision:** Must fix
**Remediation approval:** Approved
**Existing work approval:** Not applicable

## Existing Work
None.

## Root Cause
A synthetic validation gap.

## Files to Change
| File | Change |
|---|---|
| `common/example.kt` | Add validation. |

## Test Plan
- **Unit:** Reject invalid input (including empty input).
- **Negative test:** Assert rejection.
- **Regression:** Preserve valid input.
"""


class AgentSpecTests(unittest.TestCase):
    def generate(self, report):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic.md"
            source.write_text(report, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("build_agent_spec.py")), str(source)],
                capture_output=True, text=True, encoding="utf-8", check=True,
            )
            output = source.with_name("synthetic.agent.md").read_text(encoding="utf-8")
            self.assertIn("Done. 1 agent spec(s).", result.stdout)
            return output

    def assert_blocked(self, report):
        output = self.generate(report)
        self.assertIn('status: "blocked (decision or verification pending)"', output)
        self.assertIn("Stop before editing code or dispatching a fix.", output)

    def test_approved_report_is_ready_and_preserves_acceptance(self):
        output = self.generate(REPORT)
        self.assertIn('status: "ready-to-fix"', output)
        self.assertIn("blocked_on: []", output)
        self.assertIn("Unit: Reject invalid input (including empty input).", output)

    def test_optional_hardening_can_be_approved(self):
        self.assertIn('status: "ready-to-fix"', self.generate(
            REPORT.replace("Must fix", "Optional hardening")))

    def test_missing_or_pending_approvals_block_dispatch(self):
        for label in ("Remediation approval", "Existing work approval"):
            line = next(line for line in REPORT.splitlines() if line.startswith(f"**{label}:**"))
            for replacement in ("", f"**{label}:** Pending"):
                with self.subTest(label=label, replacement=replacement):
                    self.assert_blocked(REPORT.replace(line, replacement))

    def test_missing_risk_or_unsettled_decision_blocks_dispatch(self):
        for decision in ("", "Needs review", "No fix"):
            with self.subTest(decision=decision):
                self.assert_blocked(REPORT.replace("Must fix", decision))

    def test_unknown_or_low_confidence_blocks_dispatch(self):
        for confidence in ("", "Low"):
            with self.subTest(confidence=confidence):
                self.assert_blocked(REPORT.replace("Confidence:** High", f"Confidence:** {confidence}"))

    def test_external_validation_blocks_without_gap_table(self):
        self.assert_blocked(REPORT.replace("External validation:** No", "External validation:** Yes"))

    def test_open_gap_blocks_even_without_external_validation(self):
        self.assert_blocked(REPORT + """
## Verification Gaps
| # | Open question (unverified) | What we need |
|---|---|---|
| 1 | Confirm the caller contract. | Documentation. |
""")

    def test_candidate_work_requires_owner_approval(self):
        for state in ("Unmerged", "Reverted"):
            report = REPORT.replace("None.", f"{state} candidate.")
            with self.subTest(state=state):
                self.assert_blocked(report)
                self.assertIn('status: "ready-to-fix"', self.generate(
                    report.replace("Existing work approval:** Not applicable",
                                   "Existing work approval:** Approved")))

    def test_missing_files_or_acceptance_blocks_dispatch(self):
        for report in (REPORT.replace("| `common/example.kt` | Add validation. |", ""),
                       REPORT.split("## Test Plan")[0]):
            with self.subTest(report=report):
                self.assert_blocked(report)

    def test_closed_out_findings_remain_closed(self):
        for disposition in ("Won't-Fix (Already-Covered)", "Won't-Fix (Fixed-Since-Filed)",
                            "Not-Fixable (By-Design)"):
            with self.subTest(disposition=disposition):
                output = self.generate(REPORT.replace("Disposition:** Keep",
                                                     f"Disposition:** {disposition}"))
                self.assertIn('status: "closed-out (no fix)"', output)
                self.assertIn("blocked_on: []", output)


if __name__ == "__main__":
    unittest.main()

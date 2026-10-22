"""Live export reviews distinguish observed actual economics from price suggestions."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from loadpilot_model.evaluation.live_audit import LiveAuditExport, audit_live

EXAMPLES = Path(__file__).resolve().parents[2] / "requirements/docs/api/examples"

# The published API examples live outside this repository. Skip the contract
# examples rather than failing the suite when they are absent.
if not EXAMPLES.is_dir():
    raise unittest.SkipTest("API example fixtures are not part of this checkout")


def fixture():
    response = json.loads((EXAMPLES / "live-offered-batch-response.json").read_text())
    selected = next(row["evaluation"] for row in response["evaluations"] if row["selected"])
    return {
        "schema_version": 1,
        "synthetic": True,
        "exported_at": "2026-10-20T12:00:00Z",
        "decisions": [
            {
                "received_at": "2026-10-16T12:00:01Z",
                "request": json.loads((EXAMPLES / "live-offered-batch-request.json").read_text()),
                "response": response,
            }
        ],
        "submissions": [
            {
                "submission_id": "submission-example-1",
                "decision_id": selected["decision_id"],
                "shipment_id": selected["shipment_id"],
                "unit_id": selected["unit_id"],
                "idempotency_key": "example-shipment-1",
                "submitted_at": "2026-10-16T12:00:02Z",
                "amount_dollars": selected["candidate_bid_dollars"],
                "status": "accepted",
                "revalidated_at": "2026-10-16T12:00:02Z",
                "authorization_passed": True,
                "operational_checks_passed": True,
                "atomic_reservation_acquired": True,
                "rate_limit_passed": True,
            }
        ],
        "outcomes": [
            {
                "submission_id": "submission-example-1",
                "shipment_id": selected["shipment_id"],
                "observed_at": "2026-10-18T12:00:00Z",
                "award_status": "awarded",
                "shipment_status": "completed",
                "completed_at": "2026-10-18T11:00:00Z",
                "actual_payout_dollars": 3200,
                "actual_loaded_cost_dollars": 2100,
                "actual_deadhead_cost_dollars": 200,
                "actual_other_cost_dollars": 100,
                "actual_cost_scope_complete": True,
            }
        ],
    }


class LiveAuditTests(unittest.TestCase):
    def audit(self, data):
        return audit_live(LiveAuditExport.model_validate(data))

    def codes(self, data):
        return {issue["code"] for issue in self.audit(data)["issues"]}

    def test_synthetic_never_counts_as_observed_profit_or_verified_fleet(self):
        report = self.audit(fixture())
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["synthetic_realized_profit_dollars"], "800.0")
        self.assertIsNone(report["observed_realized_profit_dollars"])
        self.assertFalse(report["historical_fleet_verified"])
        self.assertFalse(report["training_data_updated"])

    def test_realized_profit_uses_only_completed_actuals_not_candidate_or_estimate(self):
        data = fixture()
        data["synthetic"] = False  # Contract behavior only; this fixture is never real evidence.
        report = self.audit(data)
        self.assertEqual(report["observed_realized_profit_dollars"], "800.0")
        for field, value in (
            ("actual_cost_scope_complete", False),
            ("actual_deadhead_cost_dollars", None),
            ("actual_payout_dollars", None),
            ("completed_at", None),
            ("award_status", "lost"),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(data)
                changed["outcomes"][0][field] = value
                report = self.audit(changed)
                self.assertIsNone(report["observed_realized_profit_dollars"])
                self.assertIn("COMPLETED_ACTUAL_ECONOMICS_INCOMPLETE", self.codes(changed))

    def test_tampering_expired_quotes_and_unselected_submissions_are_detected(self):
        for field, value, code in (
            ("amount_dollars", 1, "SUBMISSION_QUOTE_OR_ASSIGNMENT_MISMATCH"),
            ("unit_id", "different-unit", "SUBMISSION_QUOTE_OR_ASSIGNMENT_MISMATCH"),
            ("submitted_at", "2026-10-16T12:01:00Z", "SUBMISSION_OUTSIDE_DECISION_VALIDITY"),
            ("decision_id", "missing", "SUBMISSION_DECISION_MISSING"),
            (
                "atomic_reservation_acquired",
                None,
                "BACKEND_SAFEGUARD_EVIDENCE_INCOMPLETE_OR_FAILED",
            ),
        ):
            with self.subTest(field=field):
                data = fixture()
                data["submissions"][0][field] = value
                self.assertIn(code, self.codes(data))
        data = fixture()
        data["decisions"][0]["request"]["policy"]["min_profit_dollars"] += 1
        self.assertIn("REQUEST_FINGERPRINT_MISMATCH", self.codes(data))
        self.assertIn("SUBMISSION_WITHOUT_VALID_SELECTED_APPROVAL", self.codes(data))

    def test_duplicate_acceptance_does_not_double_count_profit(self):
        data = fixture()
        duplicate = copy.deepcopy(data["submissions"][0])
        duplicate["submission_id"] = "duplicate-accepted"
        data["submissions"].append(duplicate)
        self.assertIn("DUPLICATE_ACCEPTED_SHIPMENT", self.codes(data))
        self.assertEqual(self.audit(data)["complete_actual_economics_count"], 0)

    def test_future_outcomes_and_duplicate_exports_are_rejected(self):
        data = fixture()
        data["outcomes"][0]["observed_at"] = "2026-10-21T12:00:00Z"
        self.assertIn("OUTCOME_TIME_INVALID", self.codes(data))
        data = fixture()
        data["decisions"].append(copy.deepcopy(data["decisions"][0]))
        with self.assertRaisesRegex(ValidationError, "Duplicate analysis_id"):
            self.audit(data)

    def test_empty_export_and_malformed_or_sensitive_fields(self):
        data = fixture()
        for key in ("decisions", "submissions", "outcomes"):
            data[key] = []
        report = self.audit(data)
        self.assertEqual(report["issues"], [])
        self.assertIsNone(report["observed_realized_profit_dollars"])
        for changes in (
            {"token": "never-export-secrets"},
            {"exported_at": "2026-10-20T12:00:00"},
            {"currency": "CAD"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.audit({**data, **changes})

    def test_cli_hashes_source_reports_issues_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "export.json", root / "audit"
            data = fixture()
            data["submissions"][0]["amount_dollars"] = 1
            source.write_text(json.dumps(data))
            command = [
                sys.executable,
                "-m",
                "loadpilot_model.evaluation.live_audit",
                "--input",
                str(source),
                "--output",
                str(output),
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            contents = (output / "report.json").read_bytes()
            report = json.loads(contents)
            self.assertEqual(len(report["source_sha256"]), 64)
            self.assertGreater(report["issue_count"], 0)
            rerun = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(rerun.returncode, 0)
            self.assertIn("FileExistsError", rerun.stderr)
            self.assertEqual((output / "report.json").read_bytes(), contents)

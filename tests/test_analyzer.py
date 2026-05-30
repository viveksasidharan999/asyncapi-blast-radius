from pathlib import Path
import unittest

from asyncapi_blast_radius.analyzer import analyze, render_reports


ROOT = Path(__file__).resolve().parent.parent


class AnalyzerTests(unittest.TestCase):
    def test_blast_radius_report_contains_impacts(self) -> None:
        reports = analyze(
            str(ROOT / "examples" / "contracts" / "orders-v1.yaml"),
            str(ROOT / "examples" / "contracts" / "orders-v2.yaml"),
            str(ROOT / "examples" / "consumers.json"),
        )

        self.assertEqual(len(reports), 1)
        report = reports[0]
        self.assertEqual(report["topic"], "orders.created.v1")
        self.assertEqual(report["riskLevel"], "high")
        self.assertTrue(
            any(change["field"] == "customer.email" for change in report["breakingChanges"])
        )
        self.assertEqual(len(report["impactedConsumers"]), 3)
        self.assertTrue(report["migrationChecklist"])
        self.assertTrue(
            any("customer.emailAddress" in item for item in report["migrationChecklist"])
        )
        self.assertTrue(
            any("Review runbook:" in item for item in report["migrationChecklist"])
        )
        self.assertTrue(
            any("deserialization" in item for item in report["migrationChecklist"])
        )

    def test_json_rendering_returns_array(self) -> None:
        reports = analyze(
            str(ROOT / "examples" / "contracts" / "orders-v1.yaml"),
            str(ROOT / "examples" / "contracts" / "orders-v2.yaml"),
            str(ROOT / "examples" / "consumers.json"),
        )

        rendered = render_reports(reports, "json")
        self.assertTrue(rendered.strip().startswith("["))


if __name__ == "__main__":
    unittest.main()

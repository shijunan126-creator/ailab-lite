"""Offline aggregation contracts; no model, renderer or application database."""

import unittest

from score import score


class StrictGateTests(unittest.TestCase):
    def sample(self, case="trend-sales", turn=1, *, components=None, **block):
        return (
            dict(case=case, turn=turn, answer="离线样本", latency_seconds=1),
            dict(
                case=case,
                turn=turn,
                kind="test",
                blocks=[dict(valid=True, closed=True, safe=True, displaysCorrectly=True, chars=10, components=components or [], **block)],
            ),
        )

    def aggregate(self, samples, reviews):
        return score([a for a, _ in samples], [c for _, c in samples], reviews)

    def test_diagnostics_and_saved_strict_flag_do_not_gate(self):
        result = self.aggregate(
            [self.sample()],
            {
                "trend-sales:1": dict(
                    wrong_statements=[],
                    restated_outside=True,
                    numeric_correct=False,
                    expression_provenance=False,
                    conclusion_compliant=False,
                    data_consistent=False,
                    strict_consistent=False,
                )
            },
        )
        self.assertEqual(result["strict_consistent"], 1)
        self.assertEqual(result["data_consistent"], 1)
        self.assertEqual(result["restated_outside"], 1)
        self.assertEqual(result["failing_cases"], [])

    def test_wrong_ui_or_prose_statement_fails_despite_other_diagnostics(self):
        for quote in ["2月线上超过线下", 'monthly > other ? "多" : "少"']:
            with self.subTest(quote=quote):
                result = self.aggregate(
                    [self.sample()],
                    {
                        "trend-sales:1": dict(
                            wrong_statements=[quote], numeric_correct=True, expression_provenance=True, data_consistent=True
                        )
                    },
                )
                self.assertEqual(result["strict_consistent"], 0)
                self.assertEqual(result["wrong_statements"], 1)
                self.assertEqual(result["failing_cases"], ["trend-sales"])

    def test_structure_controls_html_and_edit_still_gate(self):
        cases = [
            ("trend-sales", 1, [], "missing"),
            ("trend-sales", 1, [], "unclosed"),
            ("trend-sales", 1, [], "invalid"),
            ("trend-sales", 1, [], "display"),
            ("trend-sales", 1, [], "unsafe"),
            ("trend-sales", 1, [], "transport"),
            ("calc-tax", 1, ["HtmlArtifact"], "controls"),
            ("edit-pie", 2, ["PieChart"], "edit"),
            ("edit-pie", 2, ["BarChart"], "wrong-component"),
            ("edit-theme", 2, ["HtmlArtifact"], "html"),
        ]
        for case, turn, components, failure in cases:
            with self.subTest(failure=failure):
                answer, check = self.sample(case, turn, components=components)
                review = dict(wrong_statements=[], edit_success=True)
                if failure == "missing":
                    check["blocks"] = []
                elif failure in {"unclosed", "invalid", "display", "unsafe"}:
                    field = {"unclosed": "closed", "invalid": "valid", "display": "displaysCorrectly", "unsafe": "safe"}[failure]
                    check["blocks"][0][field] = False
                elif failure == "transport":
                    answer["error"] = "offline failure"
                elif failure == "edit":
                    review["edit_success"] = False
                elif failure == "html":
                    review["html_success"] = False
                result = self.aggregate([(answer, check)], {f"{case}:{turn}": review})
                self.assertEqual(result["strict_consistent"], 0)
                self.assertEqual(result["failing_cases"], [case])

    def test_non_data_cases_keep_own_gates_and_baselines_stay_out_of_denominator(self):
        no_ui = self.sample("text-only")
        no_ui[1]["blocks"] = []
        samples = [
            no_ui,
            self.sample("html-sort", components=["HtmlArtifact"]),
            self.sample("edit-pie", components=["BarChart"]),
            self.sample("edit-pie", 2, components=["PieChart"]),
        ]
        result = self.aggregate(
            samples,
            {
                "text-only:1": dict(wrong_statements=["诊断"], restated_outside=True),
                "html-sort:1": dict(wrong_statements=["诊断"], html_success=True),
                "edit-pie:1": dict(wrong_statements=["初始轮错误"]),
                "edit-pie:2": dict(wrong_statements=[], edit_success=True),
            },
        )
        self.assertEqual(result["data_reviewed"], 1)
        self.assertEqual(result["strict_consistent"], 1)
        self.assertEqual(result["failing_cases"], ["edit-pie"])
        self.assertTrue(all(d["strict_consistent"] for d in result["details"][:2]))

    def test_missing_review_is_unknown_not_pass(self):
        result = self.aggregate([self.sample()], {})
        self.assertIsNone(result["details"][0]["strict_consistent"])
        self.assertEqual(result["semantic_unreviewed"], 1)
        self.assertEqual(result["strict_consistent"], 0)

    def test_review_field_types(self):
        for review in [dict(wrong_statements=False), dict(wrong_statements=[""]), dict(restated_outside="false")]:
            with self.subTest(review=review), self.assertRaises(ValueError):
                self.aggregate([self.sample()], {"trend-sales:1": review})


if __name__ == "__main__":
    unittest.main()

import copy
import importlib.util
import itertools
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("ci_gate", Path(__file__).parents[1] / "scripts/ci_gate.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def jobs_for(code="true", backend="true", e2e="true"):
    outputs = {"code": code, "backend": backend, "e2e": e2e}
    return {
        "changes": {"result": "success", "outputs": outputs},
        **{name: {"result": "success" if outputs[flag] == "true" else "skipped"} for name, flag in module.SELECTED.items()},
    }


class GateTests(unittest.TestCase):
    def test_every_suite_result_combination_for_each_valid_scope(self):
        # Exhaust all 4**len(SELECTED) terminal result combinations for full, Portal and docs PRs.
        for flags in [("true", "true", "true"), ("true", "false", "true"), ("false", "false", "false")]:
            baseline = jobs_for(*flags)
            for results in itertools.product(("success", "failure", "cancelled", "skipped"), repeat=len(module.SELECTED)):
                jobs = copy.deepcopy(baseline)
                expected = True
                for (name, flag), result in zip(module.SELECTED.items(), results, strict=True):
                    jobs[name]["result"] = result
                    allowed = {"success"} if jobs["changes"]["outputs"][flag] == "true" else {"success", "skipped"}
                    expected &= result in allowed
                with self.subTest(flags=flags, results=results):
                    if expected:
                        module.validate(jobs)
                    else:
                        with self.assertRaisesRegex(ValueError, "CI failed"):
                            module.validate(jobs)

    def test_selector_must_succeed_even_for_documentation(self):
        for result in ("failure", "cancelled", "skipped"):
            jobs = jobs_for("false", "false", "false")
            jobs["changes"]["result"] = result
            with self.subTest(result=result), self.assertRaisesRegex(ValueError, f"changes={result}"):
                module.validate(jobs)

    def test_all_missing_or_unrecognized_jobs_are_rejected(self):
        for name in jobs_for():
            jobs = jobs_for()
            del jobs[name]
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Invalid CI jobs"):
                module.validate(jobs)
        jobs = jobs_for()
        jobs["new-suite"] = {"result": "success"}
        for value in (jobs, None, [], "success", 1):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Invalid CI jobs"):
                module.validate(value)

    def test_malformed_job_objects_and_nonterminal_results(self):
        for name in jobs_for():
            for value in (None, [], "success", {}, {"result": []}, {"result": True}, {"result": "queued"}, {"result": ""}):
                jobs = jobs_for()
                jobs[name] = value
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, f"Invalid job result: {name}"):
                    module.validate(jobs)

    def test_scope_outputs_fail_closed_on_missing_extra_or_wrong_types(self):
        values = [None, [], "true", {}, {"code": "true", "backend": "true", "e2e": "true", "extra": "false"}]
        for key in ("code", "backend", "e2e"):
            outputs = jobs_for()["changes"]["outputs"]
            del outputs[key]
            values.append(outputs)
            for invalid in (True, False, None, 0, 1, [], {}, "TRUE", "False", "", "true\n"):
                values.append({**jobs_for()["changes"]["outputs"], key: invalid})
        for outputs in values:
            jobs = jobs_for()
            jobs["changes"]["outputs"] = outputs
            with self.subTest(outputs=outputs), self.assertRaisesRegex(ValueError, "Invalid scope outputs"):
                module.validate(jobs)

    def test_inconsistent_boolean_selections_are_rejected(self):
        valid = {("true", "true", "true"), ("true", "false", "true"), ("false", "false", "false")}
        for flags in itertools.product(("true", "false"), repeat=3):
            if flags not in valid:
                with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, "inconsistent suite selection"):
                    module.validate(jobs_for(*flags))

    def test_failure_message_names_each_failed_or_unexpectedly_skipped_job(self):
        jobs = jobs_for()
        jobs["quality"]["result"] = "failure"
        jobs["python"]["result"] = "cancelled"
        jobs["e2e"]["result"] = "skipped"
        with self.assertRaisesRegex(ValueError, "CI failed: e2e=skipped, python=cancelled, quality=failure"):
            module.validate(jobs)

    def test_cli_exit_codes_and_diagnostics(self):
        for raw, message in (
            (None, "Missing RESULTS"),
            ("", "Missing RESULTS"),
            ("{", "CI gate:"),
            ("null", "Invalid CI jobs"),
            (json.dumps(jobs_for()), "All selected checks passed."),
        ):
            env = {key: value for key, value in os.environ.items() if key != "RESULTS"}
            if raw is not None:
                env["RESULTS"] = raw
            result = subprocess.run([sys.executable, module.__file__], env=env, capture_output=True, text=True, check=False)
            success = message == "All selected checks passed."
            with self.subTest(raw=raw):
                self.assertEqual(result.returncode, 0 if success else 1)
                self.assertIn(message, result.stdout if success else result.stderr)
                self.assertEqual(result.stderr if success else result.stdout, "")
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()

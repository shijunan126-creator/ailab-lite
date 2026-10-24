import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("verify_junit", Path(__file__).parents[1] / "scripts/verify_junit.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class JUnitTests(unittest.TestCase):
    def verify(self, body, required=("contract::",), allowed=()):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.xml"
            path.write_text(body)
            MODULE.verify(path, required, allowed)

    def test_accepts_success(self):
        self.verify('<testsuites><testsuite><testcase classname="contract" name="s3"/></testsuite></testsuites>')

    def test_missing_empty_malformed_and_failed_groups(self):
        for xml in (
            "<testsuites/>",
            '<testsuite><testcase classname="other" name="s3"/></testsuite>',
            '<testsuite><testcase classname="contract" name="s3"><skipped/></testcase></testsuite>',
            '<testsuite><testcase classname="contract" name="s3"><failure/></testcase></testsuite>',
            '<testsuite><testcase classname="contract" name="s3"><error/></testcase></testsuite>',
        ):
            with self.subTest(xml=xml), self.assertRaises(ValueError):
                self.verify(xml)
        with self.assertRaises(Exception):
            self.verify("broken xml")

    def test_allowed_skip_does_not_allow_an_empty_group(self):
        xml = '<testsuite><testcase classname="contract" name="local-only"><skipped/></testcase>{}</testsuite>'
        with self.assertRaises(ValueError):
            self.verify(xml.format(""), allowed=("local-only$",))
        self.verify(xml.format('<testcase classname="contract" name="s3"/>'), allowed=("local-only$",))

    def test_every_required_group_is_checked(self):
        with self.assertRaises(ValueError):
            self.verify('<testsuite><testcase classname="contract" name="s3"/></testsuite>', ("contract::", "postgres"))

    def test_missing_report_fails(self):
        with self.assertRaises(FileNotFoundError):
            MODULE.verify("/no-such-ci-report.xml", ["contract"])


if __name__ == "__main__":
    unittest.main()

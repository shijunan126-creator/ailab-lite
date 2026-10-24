"""Require real-backend contract groups to execute successfully, rather than silently skip."""

import argparse
import re
import xml.etree.ElementTree as ET


def verify(path, required, allowed_skips=()):
    cases = list(ET.parse(path).getroot().iter("testcase"))
    if not cases:
        raise ValueError("JUnit report contains no test cases")
    allow = [re.compile(pattern) for pattern in allowed_skips]
    for pattern in required:
        expression = re.compile(pattern)
        matched = [case for case in cases if expression.search(case.get("classname", "") + "::" + case.get("name", ""))]
        if not matched:
            raise ValueError(f"No tests matched required group: {pattern}")
        passed = 0
        for case in matched:
            identity = case.get("classname", "") + "::" + case.get("name", "")
            if case.find("failure") is not None or case.find("error") is not None:
                raise ValueError(f"Required contract failed: {identity}")
            if case.find("skipped") is not None:
                if not any(p.search(identity) for p in allow):
                    raise ValueError(f"Required contract skipped: {identity}")
            else:
                passed += 1
        if not passed:
            raise ValueError(f"Required group has no successful tests: {pattern}")
        print(f"Required group passed: {pattern} ({passed} tests)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    parser.add_argument("--require", action="append", required=True)
    parser.add_argument("--allow-skip", action="append", default=[])
    args = parser.parse_args()
    verify(args.report, args.require, args.allow_skip)


if __name__ == "__main__":
    main()

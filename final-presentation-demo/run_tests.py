#!/usr/bin/env python3
"""Discover and run the lending-system test suite.

Prints a one-line SUMMARY and a final PASS / FAILED marker, and exits with
code 0 only when every test passes. No third-party dependencies.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    sys.path.insert(0, ROOT)
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=os.path.join(ROOT, "tests"), top_level_dir=ROOT)
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed

    print("-" * 48)
    print(f"SUMMARY: {passed}/{total} passed, {failed} failed")
    if failed == 0:
        print("PASS")
        return 0
    print("FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())

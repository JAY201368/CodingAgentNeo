"""Standalone verification for the buggy_counter fixture.

This file is not a pytest test module. Agent drills execute it as a command.
"""

from __future__ import annotations

from counter import increment


def main() -> None:
    assert increment(0) == 1, increment(0)
    assert increment(4) == 5, increment(4)
    print("ok")


if __name__ == "__main__":
    main()

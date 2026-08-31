"""Mark every test under tests/acceptance as acceptance evidence."""

from __future__ import annotations

from pathlib import Path

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    for item in items:
        path = Path(str(item.path))
        if path.parent.name == "acceptance":
            item.add_marker(pytest.mark.acceptance)

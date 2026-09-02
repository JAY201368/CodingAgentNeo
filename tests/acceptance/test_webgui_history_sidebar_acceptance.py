"""Static evidence for the Web history-sidebar delivery.

These checks inspect already-landed Web sources, README wording, and the
tracked tree. They do not start a browser, call a live model, or change
Python product code.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_SRC = REPO_ROOT / "web" / "src"
README = REPO_ROOT / "README.md"
PERSIST_HELPER = WEB_SRC / "composables" / "useAgentSession.ts"

PRODUCT_SUFFIXES = {".ts", ".vue"}
SPEC_SUFFIX = ".spec.ts"
FETCH_CALL = re.compile(r"\bfetch\s*\(")
PYTHON_IMPORT = re.compile(
    r"""(?:from|import)\s+['"](?:coding_agent(?:_neo)?|coding-agent-neo)['"]"""
)
V_HTML = re.compile(r"\bv-html\b")
SET_ITEM = re.compile(r"\.setItem\s*\(")
LOCAL_STORAGE = re.compile(r"\blocalStorage\b")
FORBIDDEN_TRACKED = (
    re.compile(r"(^|/)web/dist(/|$)"),
    re.compile(r"(^|/)node_modules(/|$)"),
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)\.coding-agent-neo\.toml$"),
)


def _product_files() -> tuple[Path, ...]:
    return tuple(
        path
        for path in WEB_SRC.rglob("*")
        if path.is_file()
        and path.suffix in PRODUCT_SUFFIXES
        and not path.name.endswith(SPEC_SUFFIX)
    )


def _tracked_files() -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [item for item in completed.stdout.split("\0") if item]


def test_history_sidebar_web_sources_stay_inside_browser_boundaries() -> None:
    product_files = _product_files()
    assert product_files

    v_html_hits: list[str] = []
    python_import_hits: list[str] = []
    fetch_hits: list[str] = []
    storage_hits: list[str] = []

    for path in product_files:
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(REPO_ROOT).as_posix()
        if V_HTML.search(source):
            v_html_hits.append(relative)
        if PYTHON_IMPORT.search(source):
            python_import_hits.append(relative)
        if FETCH_CALL.search(source) and path.name != "client.ts":
            fetch_hits.append(relative)
        if path != PERSIST_HELPER and (SET_ITEM.search(source) or LOCAL_STORAGE.search(source)):
            storage_hits.append(relative)

    persist_source = PERSIST_HELPER.read_text(encoding="utf-8")
    assert "transportSessionId: value.transportSessionId" in persist_source
    assert "cursor: value.cursor" in persist_source
    assert "historySessionId" not in persist_source.split("storage.setItem", 1)[1][:400]

    assert v_html_hits == []
    assert python_import_hits == []
    assert fetch_hits == []
    assert storage_hits == []


def test_history_sidebar_readme_documents_resume_and_keeps_secrets_out() -> None:
    readme = README.read_text(encoding="utf-8")
    folded = re.sub(r"\s+", " ", readme.casefold())
    assert "only loads the history list" in folded
    assert "does not automatically create or attach" in folded
    assert "circular sidebar" in folded
    assert "history item" in folded
    assert "resume_session_id" in readme
    assert "DELETE" in readme
    assert "persisted" in folded and "hint" in folded
    assert "session_end" in readme
    assert "history projection" in folded
    assert "finite JSON" in readme or "有限 JSON" in readme
    assert "scroll independently" in folded
    assert "has no end session" in folded
    assert "reconnect" in folded
    assert "new-session buttons" in folded
    assert "one active transport session" in readme or "one linear transport session" in readme
    assert "does not recreate" in readme or "does not recreate a session automatically" in readme
    assert "api key" in folded
    assert "--api-key" in readme
    assert "or server-restart Web resume" not in readme
    assert "a browser refresh only reconnects" not in folded
    assert "automatically creates a session" not in folded
    assert "end session button" not in folded
    assert "reconnect events" not in folded


def test_history_sidebar_tracked_tree_excludes_build_secrets_and_sessions() -> None:
    tracked = _tracked_files()
    violations = [
        path for path in tracked if any(pattern.search(path) for pattern in FORBIDDEN_TRACKED)
    ]
    assert violations == []
    assert not any(path.endswith(".session.jsonl") for path in tracked)
    assert "web/dist/index.html" not in tracked
    assert "web/node_modules/vue/package.json" not in tracked

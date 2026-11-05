"""The rules in AGENTS.md that ruff and mypy cannot check on their own."""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sorted([*(ROOT / "src").rglob("*.py"), *(ROOT / "tests").rglob("*.py")])
DOMAIN = sorted((ROOT / "src" / "tripbot" / "domain").rglob("*.py"))

# Built from parts so this file does not match itself.
SILENCERS = re.compile("#" + r"\s*(noqa|type:\s*ignore)")
DOMAIN_IMPORTS = ("httpx", "sqlite3", "os", "subprocess", "playwright", "jinja2", "yaml")
DOMAIN_PACKAGES = ("tripbot.store", "tripbot.sources", "tripbot.pipeline", "tripbot.telegram")
CLOCK_CALLS = ("now", "today", "utcnow")


def _name(path: Path) -> str:
    return str(path.relative_to(ROOT))


@pytest.mark.parametrize("path", PYTHON, ids=_name)
def test_no_silenced_checks(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    assert not SILENCERS.search(source), "fix the code instead of silencing the check"
    tree = ast.parse(source)
    casts = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name | ast.Attribute)
        and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr) == "cast"
    ]
    assert not casts, f"typing.cast on lines {casts}"


@pytest.mark.parametrize("path", DOMAIN, ids=_name)
def test_domain_has_no_io_and_no_clock(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            modules = []
        for module in modules:
            assert module.split(".")[0] not in DOMAIN_IMPORTS, f"domain imports {module}"
            assert not module.startswith(DOMAIN_PACKAGES), f"domain imports {module}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in CLOCK_CALLS, f"domain reads the clock: line {node.lineno}"

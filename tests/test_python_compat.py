"""Guard the declared Python floor.

``pyproject.toml`` declares ``requires-python >= 3.9`` and CI runs a 3.9 job,
so every annotation that PEP 604 turns into an expression (``str | None``)
must be deferred by ``from __future__ import annotations``. Without it the
``|`` is evaluated when the annotation is created, which raises
``TypeError: unsupported operand type(s) for |`` on 3.9 — at import time for
module-level classes and at definition time for functions.
"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "cascade_compression"
FUTURE_IMPORT = "from __future__ import annotations"


def _has_future_annotations(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(alias.name == "annotations" for alias in node.names):
                return True
    return False


def _annotations(node: ast.AST):
    if isinstance(node, ast.AnnAssign):
        yield node.annotation
    elif isinstance(node, ast.arg):
        if node.annotation is not None:
            yield node.annotation
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if node.returns is not None:
            yield node.returns


def _contains_pep604(annotation: ast.AST) -> bool:
    return any(
        isinstance(child, ast.BinOp) and isinstance(child.op, ast.BitOr)
        for child in ast.walk(annotation)
    )


def pep604_without_future(source: str) -> bool:
    """True when ``source`` uses a PEP 604 annotation without deferring it."""
    tree = ast.parse(source)
    if _has_future_annotations(tree):
        return False
    return any(
        _contains_pep604(annotation)
        for node in ast.walk(tree)
        for annotation in _annotations(node)
    )


def test_package_defers_every_pep604_annotation():
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted(PACKAGE.rglob("*.py"))
        if pep604_without_future(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_undeferred_annotation_is_detected():
    assert pep604_without_future("class T:\n    kind: str | None = None\n")
    assert pep604_without_future("def f(x: int | None) -> None:\n    pass\n")


def test_deferred_annotation_passes():
    assert not pep604_without_future(
        "from __future__ import annotations\n\nclass T:\n    kind: str | None = None\n"
    )


def test_bitwise_or_outside_an_annotation_is_ignored():
    assert not pep604_without_future("MASK = 1 | 2\n")

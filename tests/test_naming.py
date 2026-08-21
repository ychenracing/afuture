"""确保代码和文件名保持英文/ASCII；中文仅用于注释、文档和用户日志。"""

from __future__ import annotations

import ast
from pathlib import Path


def test_python_paths_and_identifiers_are_ascii():
    root = Path("afuture")
    for path in root.rglob("*.py"):
        assert path.as_posix().isascii(), path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            values = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                values.append(node.name)
            if isinstance(node, ast.Name):
                values.append(node.id)
            if isinstance(node, ast.arg):
                values.append(node.arg)
            if isinstance(node, ast.Attribute):
                values.append(node.attr)
            for value in values:
                assert value.isascii(), f"{path}: non-ASCII identifier {value!r}"

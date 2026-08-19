from pathlib import Path
import tokenize


def test_python_identifiers_and_file_names_are_ascii():
    root = Path(__file__).resolve().parents[1]
    for path in root.rglob("*"):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        assert all(ord(char) < 128 for char in path.name), f"non-ASCII file name: {path}"
        if path.suffix != ".py":
            continue
        with path.open("rb") as handle:
            for token in tokenize.tokenize(handle.readline):
                if token.type == tokenize.NAME:
                    assert token.string.isascii(), f"non-ASCII identifier {token.string!r} in {path}"

from mutagen.testgen.tier1 import _strip_fences


def test_strip_fences_python_tag():
    src = "```python\nimport pytest\n\ndef test_x(): pass\n```"
    out = _strip_fences(src)
    assert out.startswith("import pytest")
    assert out.endswith("\n")
    assert "```" not in out


def test_strip_fences_bare():
    src = "```\nx = 1\n```"
    out = _strip_fences(src)
    assert out == "x = 1\n"


def test_strip_fences_no_fences_appends_newline():
    src = "x = 1"
    out = _strip_fences(src)
    assert out == "x = 1\n"


def test_strip_fences_preserves_trailing_newline():
    src = "x = 1\n"
    out = _strip_fences(src)
    assert out == "x = 1\n"


def test_strip_fences_ignores_leading_whitespace():
    src = "   \n```python\nx = 1\n```\n   "
    out = _strip_fences(src)
    assert out == "x = 1\n"

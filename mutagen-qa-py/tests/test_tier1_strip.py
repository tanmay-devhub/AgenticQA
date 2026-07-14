from mutagen.testgen.tier1 import _strip_fences, has_tests


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


def test_has_tests_true_for_module_level_def():
    assert has_tests("import pytest\n\ndef test_x():\n    assert True\n")


def test_has_tests_true_for_async_def():
    assert has_tests("async def test_async_ok(): pass\n")


def test_has_tests_true_for_indented_def():
    """Class-nested test functions still count; pytest discovers them."""
    src = "class TestGroup:\n    def test_a(self):\n        assert True\n"
    assert has_tests(src)


def test_has_tests_false_for_empty_source():
    assert not has_tests("")
    assert not has_tests("\n\n   \n")


def test_has_tests_false_for_only_imports():
    assert not has_tests("import pytest\nfrom target import slugify\n")


def test_has_tests_false_for_only_thinking_output():
    """Reasoner models sometimes emit `<think>` prose and never reach code."""
    src = "<think>\nLet me consider the max_len boundary...\n</think>\n"
    assert not has_tests(src)


def test_has_tests_false_for_non_test_def():
    """`def helper()` isn't a pytest test -- only `def test_*` counts."""
    assert not has_tests("def helper():\n    return 42\n")

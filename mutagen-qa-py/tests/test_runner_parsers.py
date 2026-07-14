from mutagen.mutation.runner import _classify_diff, _parse_file_and_line


COMPARISON_DIFF = """--- target.py
+++ target.py
@@ -20,7 +20,7 @@
 def clamp(v, lo, hi):
-    if v < lo:
+    if v <= lo:
         return lo
"""

ARITHMETIC_DIFF = """--- target.py
+++ target.py
@@ -60,7 +60,7 @@
-    return range(lo, hi + 1)
+    return range(lo, hi - 1)
"""

CONSTANT_NUM_DIFF = """--- target.py
+++ target.py
@@ -30,7 +30,7 @@
-    sep_idx = -1
+    sep_idx = 2
"""

CONSTANT_INDEX_DIFF = """--- target.py
+++ target.py
@@ -50,7 +50,7 @@
-    rest = right[1:]
+    rest = right[2:]
"""

BOOLEAN_DIFF = """--- target.py
+++ target.py
@@ -10,7 +10,7 @@
-    if a and b:
+    if a or b:
         return 1
"""

RETURN_DIFF = """--- target.py
+++ target.py
@@ -5,7 +5,7 @@
 def f():
-    return x
+    return None
"""

KEYWORD_DIFF = """--- target.py
+++ target.py
@@ -3,7 +3,7 @@
     for x in xs:
-        break
+        continue
"""


def test_classify_comparison():
    assert _classify_diff(COMPARISON_DIFF) == "comparison"


def test_classify_arithmetic():
    assert _classify_diff(ARITHMETIC_DIFF) == "arithmetic"


def test_classify_constant_negative_number():
    # `-1` -> `2` must be `constant`, not `arithmetic` (unary minus binds to literal).
    assert _classify_diff(CONSTANT_NUM_DIFF) == "constant"


def test_classify_constant_index():
    assert _classify_diff(CONSTANT_INDEX_DIFF) == "constant"


def test_classify_boolean():
    assert _classify_diff(BOOLEAN_DIFF) == "boolean"


def test_classify_return():
    assert _classify_diff(RETURN_DIFF) == "return"


def test_classify_keyword():
    assert _classify_diff(KEYWORD_DIFF) == "keyword"


def test_classify_empty_diff_is_other():
    assert _classify_diff("") == "other"


def test_parse_file_and_line_basic():
    file, line = _parse_file_and_line(COMPARISON_DIFF)
    assert file == "target.py"
    # Hunk starts at new-side line 20. Context ` def clamp` occupies line 20,
    # then the `-`/`+` pair modifies line 21 in the new file.
    assert line == 21


def test_parse_file_strips_a_b_prefix():
    diff = "--- a/target.py\n+++ b/target.py\n@@ -1,2 +1,2 @@\n line\n-old\n+new\n"
    file, line = _parse_file_and_line(diff)
    assert file == "target.py"
    assert line == 2


def test_parse_file_missing_hunk_returns_none_line():
    diff = "+++ target.py\n"
    file, line = _parse_file_and_line(diff)
    assert file == "target.py"
    assert line is None

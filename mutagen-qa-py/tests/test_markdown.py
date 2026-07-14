"""Tests for the in-tree Markdown renderer.

Not a CommonMark compliance suite -- just the features the debrief file
actually uses. If we ever need more, swap in ``markdown-it-py`` and delete
this module.
"""

from __future__ import annotations

from mutagen.web.markdown import render


def test_heading_levels() -> None:
    out = render("# H1\n\n## H2\n\n### H3\n")
    assert "<h1>H1</h1>" in out
    assert "<h2>H2</h2>" in out
    assert "<h3>H3</h3>" in out


def test_paragraph_wraps_and_joins_wrapped_lines() -> None:
    out = render("first line\nsecond line\n")
    assert "<p>first line second line</p>" in out


def test_bullets_render_as_ul_li() -> None:
    out = render("- one\n- two\n- three\n")
    assert out.count("<ul>") == 1
    assert out.count("</ul>") == 1
    assert out.count("<li>") == 3


def test_bold_and_italics() -> None:
    out = render("this is **bold** and *italic*.\n")
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out


def test_inline_code() -> None:
    out = render("call `parse_range` to get a range.\n")
    assert "<code>parse_range</code>" in out


def test_fenced_code_block_with_language() -> None:
    out = render("```diff\n- a\n+ b\n```\n")
    assert '<pre><code class="language-diff">' in out
    # Content is HTML-escaped.
    assert "- a" in out and "+ b" in out


def test_html_in_source_is_escaped() -> None:
    """Debriefs never contain user HTML today, but we must not open that
    door by accident."""
    out = render("- <script>alert(1)</script>\n")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_bold_inside_bullet() -> None:
    out = render("- `real_gap`: **1**\n")
    assert "<li>" in out and "<strong>1</strong>" in out
    assert "<code>real_gap</code>" in out


def test_horizontal_rule() -> None:
    assert "<hr>" in render("---\n")

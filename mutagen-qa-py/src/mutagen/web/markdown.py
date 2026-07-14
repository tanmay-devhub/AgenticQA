"""Minimal Markdown -> HTML renderer.

Just enough of CommonMark to render the debrief files well: headings,
lists, fenced code blocks with a language hint, ``inline code``, bold,
italics, and horizontal rules. No links, no tables, no HTML pass-through
(everything is escaped -- debriefs never contain user-controlled HTML,
but escaping-by-default keeps future changes safe).

Not a replacement for a real Markdown library. Kept in-tree so the web
layer doesn't pick up ``markdown-it-py`` or ``mistune`` for one page.
"""

from __future__ import annotations

import html
import re

_H_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_ULI_RE = re.compile(r"^(\s*)[-*]\s+(.+)$")
_INDENT = 2  # nested list step

_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")


def _inline(text: str) -> str:
    """Apply inline transforms on already-escaped text.

    Order matters: inline code first (to protect its contents from bold /
    italic), then bold, then italics. Simple regexes suffice because the
    debrief markdown we produce is well-formed and controlled.
    """
    def _code(m: re.Match) -> str:
        # ``m.group(1)`` is already-escaped user content; re-wrap as code.
        return f"<code>{m.group(1)}</code>"

    text = _INLINE_CODE_RE.sub(_code, text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)
    return text


def render(md: str) -> str:
    """Return an HTML string. The output is intended to be embedded inside
    a wrapper element that already has scoped styles."""
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    in_list_stack: list[int] = []  # indent depths of open <ul>s

    def _close_lists(down_to: int = -1) -> None:
        while in_list_stack and in_list_stack[-1] > down_to:
            out.append("</ul>")
            in_list_stack.pop()

    while i < len(lines):
        line = lines[i]

        # Fenced code block: ```lang ... ```
        if line.startswith("```"):
            _close_lists()
            lang = line[3:].strip()
            cls = f' class="language-{html.escape(lang)}"' if lang else ""
            i += 1
            buf: list[str] = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(html.escape(lines[i]))
                i += 1
            i += 1  # skip closing fence
            out.append(f"<pre><code{cls}>" + "\n".join(buf) + "</code></pre>")
            continue

        # Heading
        m = _H_RE.match(line)
        if m:
            _close_lists()
            level = len(m.group(1))
            body = _inline(html.escape(m.group(2).strip()))
            out.append(f"<h{level}>{body}</h{level}>")
            i += 1
            continue

        # Horizontal rule
        if re.match(r"^\s{0,3}(?:-{3,}|_{3,}|\*{3,})\s*$", line):
            _close_lists()
            out.append("<hr>")
            i += 1
            continue

        # Unordered list item
        mli = _ULI_RE.match(line)
        if mli:
            indent = len(mli.group(1))
            item_html = _inline(html.escape(mli.group(2).strip()))
            # Open / close nested <ul>s to match indent depth.
            depth = indent // _INDENT
            while len(in_list_stack) <= depth:
                out.append("<ul>")
                in_list_stack.append(len(in_list_stack) * _INDENT)
            while in_list_stack and in_list_stack[-1] > indent:
                out.append("</ul>")
                in_list_stack.pop()
            out.append(f"<li>{item_html}</li>")
            i += 1
            continue

        # Blank line: close any open lists but keep paragraph flow intact.
        if not line.strip():
            _close_lists()
            i += 1
            continue

        # Paragraph: consume until blank or block boundary.
        _close_lists()
        buf = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not (
            lines[i].startswith("```")
            or _H_RE.match(lines[i])
            or _ULI_RE.match(lines[i])
        ):
            buf.append(lines[i])
            i += 1
        para = " ".join(s.strip() for s in buf)
        out.append(f"<p>{_inline(html.escape(para))}</p>")

    _close_lists()
    return "\n".join(out)

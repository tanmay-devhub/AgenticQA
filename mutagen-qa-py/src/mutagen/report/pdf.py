"""HTML -> PDF for the report page.

We use ``xhtml2pdf`` (pure Python) because the web-app target is Windows
first, and ``weasyprint`` needs GTK/Cairo/Pango which are painful to
install there. xhtml2pdf's CSS support is more limited than weasyprint's,
so the print-friendly rules under ``@media print`` in style.css are the
authoritative styling for the PDF -- the report template uses conservative
markup that xhtml2pdf handles cleanly.
"""

from __future__ import annotations

from io import BytesIO


def html_to_pdf(html: str) -> bytes:
    """Return PDF bytes for the given standalone HTML string.

    Raises RuntimeError if xhtml2pdf reports any parse error, so the caller
    can return a 500 with a clear message instead of streaming a broken PDF.
    """
    # Import here so the web app still boots when xhtml2pdf is missing (the
    # HTML view still works, only the PDF endpoint 500s).
    from xhtml2pdf import pisa

    buf = BytesIO()
    result = pisa.CreatePDF(html, dest=buf, encoding="utf-8")
    if result.err:
        raise RuntimeError(f"xhtml2pdf reported {result.err} error(s) rendering the report")
    return buf.getvalue()

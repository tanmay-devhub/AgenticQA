"""Post-run analysis: LLM-driven judgment on each surviving mutant + PDF export.

The runner produces mutation counts; this package produces the *why* -- explaining
which survivors are real test gaps vs equivalent mutants, rating severity, and
recommending fixes. Output is an ``AnalysisReport`` persisted as ``analysis.json``
in the workdir; the web layer renders both HTML and PDF views from it.
"""

from mutagen.report.analysis import (
    AnalysisReport,
    SurvivorAnalysis,
    Severity,
    Category,
    analyze_run,
)

__all__ = [
    "AnalysisReport",
    "SurvivorAnalysis",
    "Severity",
    "Category",
    "analyze_run",
]

"""Mutation-testing engine integration.

Responsibility: run mutmut against a target module + its current test suite,
parse results into a structured report (killed / survived / timeout / suspicious),
and expose survivor metadata (file, line, mutation kind, original vs mutated
snippet) so the planner can steer new tests.

Modules:
    runner  -- invoke mutmut, capture cache, produce MutationReport.
    report  -- typed dataclasses for mutants, results, kill-rate.
"""

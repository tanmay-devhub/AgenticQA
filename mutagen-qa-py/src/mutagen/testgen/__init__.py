"""Test generators, split by tier.

Tier 1: happy-path, example-based, parameterized/table-driven.
Tier 2: boundary, error-path, negative, regression, characterization.
Tier 3: property-based (Hypothesis), metamorphic, fuzz, concurrency, security.

Each tier module exports a `generate(spec, target_source, context) -> str`
returning pytest source. The planner picks which tier to invoke per gap.
"""

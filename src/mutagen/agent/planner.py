"""Planner: choose the next test-generation target and technique.

Given (surviving mutants, coverage report, prior attempts), decide:
  - which mutant(s) to attack next (cluster by location / mutation kind),
  - which tier (1/2/3) and technique (example, boundary, property, ...) fits,
  - the prompt/spec to hand to the test generator.

Stateless w.r.t. execution; consumes reports, emits plans.
"""

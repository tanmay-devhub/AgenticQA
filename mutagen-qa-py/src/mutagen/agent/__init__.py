"""Agent package.

Responsibility: orchestrate the closed loop
    plan -> generate tests -> run suite -> mutate -> inspect survivors ->
    pick a technique -> generate targeted tests -> repeat until plateau.

Modules:
    planner  -- decides what to attack next given surviving mutants + coverage.
    loop     -- the run-until-plateau driver; owns stopping criteria.
    llm      -- provider-agnostic LLM client (Ollama or API), config-driven.
"""

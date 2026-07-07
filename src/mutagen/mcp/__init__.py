"""MCP surface: expose the agent as tools over the Model Context Protocol.

Tools (planned):
    qa.generate_tests(target_path, budget, config) -> {tests, report}
    qa.mutation_score(target_path, tests_path)     -> {kill_rate, survivors}
    qa.run_loop(target_path, budget, config)       -> streamed progress

Kept in its own subpackage so the agent library stays usable without MCP.
"""

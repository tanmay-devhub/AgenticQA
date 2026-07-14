"""MCP server exposing mutagen's core operations as agent-callable tools.

Design: thin wrappers around the same functions the CLI uses. If it works from
the CLI, it works over MCP -- no reimplementation, no divergence.
"""

from mutagen.mcp.server import create_server

__all__ = ["create_server"]

#!/usr/bin/env bash
# sequential-thinking-wrapper.sh
#
# Launches the official Anthropic @modelcontextprotocol/server-sequential-thinking
# MCP server over stdio. This is the reference implementation of Anthropic's
# `think` tool (+54% policy adherence on agentic tasks; see
# https://www.anthropic.com/engineering/claude-think-tool).
#
# The upstream server only supports stdio, so it is launched per-client rather
# than as a long-running systemd service. Chimère's own `chimere.think` tool
# (in chimere-mcp.py) is an HTTP-exposed mirror for clients that prefer HTTP.
#
# Usage:
#   ./sequential-thinking-wrapper.sh            # speak MCP stdio protocol
#   mcp-client --server "~/.openclaw/mcp/sequential-thinking-wrapper.sh"
set -euo pipefail
exec mcp-server-sequential-thinking "$@"

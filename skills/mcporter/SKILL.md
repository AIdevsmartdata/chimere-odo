---
name: mcporter
description: MCP CLI — list, configure, auth, and call MCP servers/tools directly (HTTP or stdio), including ad-hoc servers, config edits, and CLI/type generation.
trigger_patterns:
  - "mcporter"
  - "mcp server"
  - "call tool"
  - "list mcp"
  - "mcp daemon"
tools_required:
  - exec
  - mcporter_cli
examples:
  - "mcporter list"
  - "mcporter list linear --schema"
  - "mcporter call linear.list_issues team=ENG limit:5"
  - "mcporter auth notion"
  - "mcporter daemon status"
homepage: "http://mcporter.dev"
install:
  - kind: node
    package: mcporter
    bins:
      - mcporter
execution:
  command: "mcporter $args"
  timeout_ms: 120000
  arg_mode: raw
---

# mcporter — MCP Server CLI

Utiliser `mcporter` pour travailler avec les serveurs MCP directement.

## Quick Start

- `mcporter list` — liste les serveurs MCP configurés
- `mcporter list <server> --schema` — schéma d'un serveur
- `mcporter call <server.tool> key=value` — appel direct d'un tool

## Call Tools

- Selector: `mcporter call linear.list_issues team=ENG limit:5`
- Function syntax: `mcporter call "linear.create_issue(title: \"Bug\")"`
- Full URL: `mcporter call https://api.example.com/mcp.fetch url:https://example.com`
- Stdio: `mcporter call --stdio "bun run ./server.ts" scrape url=https://example.com`
- JSON payload: `mcporter call <server.tool> --args '{"limit":5}'`

## Auth + Config

- OAuth: `mcporter auth <server | url> [--reset]`
- Config: `mcporter config list|get|add|remove|import|login|logout`

## Daemon

- `mcporter daemon start|status|stop|restart`

## Codegen

- CLI: `mcporter generate-cli --server <name>` ou `--command <url>`
- Inspect: `mcporter inspect-cli <path> [--json]`
- TS: `mcporter emit-ts <server> --mode client|types`

## Notes

- Config default: `./config/mcporter.json` (override with `--config`).
- Prefer `--output json` for machine-readable results.

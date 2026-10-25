#!/usr/bin/env bash
# One-shot setup: create venv, install deps, store LMS credentials in the OS keychain,
# and register the MCP server with Claude Code.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/.venv/bin/python3"

if [ ! -x "$PY" ]; then
  echo "→ Creating virtualenv"
  python3 -m venv "$DIR/.venv"
fi

echo "→ Installing dependencies"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -r "$DIR/requirements.txt"

echo "→ Logging in to NUST LMS"
"$PY" "$DIR/src/cli.py" login

if command -v claude >/dev/null 2>&1; then
  echo "→ Registering 'nust-lms' with Claude Code"
  claude mcp remove nust-lms --scope user >/dev/null 2>&1 || true
  claude mcp add nust-lms --scope user -- "$PY" "$DIR/src/server.py"
  echo "✅ Done. Start a new Claude Code session (or run /mcp to reconnect) and ask: \"what submissions do I have left?\""
else
  echo "✅ Done. Claude Code not found; add this server to your MCP client manually:"
  echo "   command: $PY"
  echo "   args:    $DIR/src/server.py"
fi

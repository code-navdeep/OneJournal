#!/usr/bin/env bash
# Compile Tailwind input.css -> app.css using the standalone CLI.
# Usage:
#   ./build-css.sh           # one-shot, minified
#   ./build-css.sh --watch   # watch mode, unminified (for local dev)

set -euo pipefail
cd "$(dirname "$0")"

BIN=".bin/tailwindcss"
INPUT="static/css/input.css"
OUTPUT="static/css/app.css"

if [[ ! -x "$BIN" ]]; then
  echo "Tailwind CLI not found at $BIN" >&2
  echo "Download it: curl -sLo $BIN https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-macos-arm64 && chmod +x $BIN" >&2
  exit 1
fi

if [[ "${1:-}" == "--watch" ]]; then
  exec "$BIN" -i "$INPUT" -o "$OUTPUT" --watch
else
  "$BIN" -i "$INPUT" -o "$OUTPUT" --minify
  echo "Compiled $OUTPUT"
fi

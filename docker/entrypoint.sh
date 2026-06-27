#!/bin/sh
set -e

# Persist the runtime config on the mounted workspace volume so dashboard edits
# (e.g. added MCP servers) survive container restarts/recreations. The image
# ships a baked default; we seed it into the volume on first run only.
CONFIG_DIR=/app/.workforce_runtime
LIVE_CONFIG="$CONFIG_DIR/workforce_runtime_config.json"
SEED_CONFIG=/app/workforce_runtime_config.json

mkdir -p "$CONFIG_DIR"
if [ ! -f "$LIVE_CONFIG" ]; then
    cp "$SEED_CONFIG" "$LIVE_CONFIG"
fi

exec workforce-runtime --config "$LIVE_CONFIG" dashboard --serve --host 0.0.0.0 --port 8765

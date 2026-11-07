#!/bin/bash
# Pod start command (set as the pod's `args`): bash /workspace/runpod-slim/boot.sh
# Adds basic auth in front of ComfyUI, disables FileBrowser, then hands off to the image's /start.sh.
# Lives on the network volume so it survives restarts and new pods. No `set -e`: whatever happens
# here, /start.sh must still run so SSH/Jupyter stay reachable.
RS=/workspace/runpod-slim

# FileBrowser: a no-op `filebrowser` earlier on PATH makes /start.sh's launch a no-op.
export PATH="$RS/shims:$PATH"

# Caddy on 0.0.0.0:8188 (basic auth) -> ComfyUI on 127.0.0.1:8189 (see comfyui_args.txt).
if [ -x "$RS/bin/caddy" ] && [ -f "$RS/Caddyfile" ]; then
  nohup "$RS/bin/caddy" run --config "$RS/Caddyfile" --adapter caddyfile > /caddy.log 2>&1 &
  echo "boot.sh: caddy started (pid $!), log /caddy.log"
else
  echo "boot.sh: WARNING caddy or Caddyfile missing; ComfyUI is NOT reachable through the proxy"
fi

exec /start.sh "$@"

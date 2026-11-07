#!/bin/bash
# One-time (idempotent) setup, run inside the pod:
#   bash setup_auth.sh '<bcrypt hash>' [user]
# - installs a pinned Caddy binary on the volume (SHA-512 checked against the release checksums)
# - writes the Caddyfile: basic auth on :8188 -> reverse proxy to ComfyUI on 127.0.0.1:8189
# - makes ComfyUI listen on 127.0.0.1:8189 via comfyui_args.txt (appended after /start.sh defaults)
# - installs a no-op `filebrowser` shim used by boot.sh to disable FileBrowser
# - installs boot.sh (copied next to this script)
# Then set the pod's start command to: bash /workspace/runpod-slim/boot.sh
set -euo pipefail

HASH="${1:?usage: setup_auth.sh '<bcrypt hash>' [user]}"
USER_NAME="${2:-comfy}"
CADDY_VERSION="2.10.2"
RS=/workspace/runpod-slim
HERE="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$RS/bin" "$RS/shims"

if ! "$RS/bin/caddy" version 2>/dev/null | grep -q "v$CADDY_VERSION"; then
  tmp="$(mktemp -d)"
  base="https://github.com/caddyserver/caddy/releases/download/v$CADDY_VERSION"
  tarball="caddy_${CADDY_VERSION}_linux_amd64.tar.gz"
  curl -fsSL -o "$tmp/$tarball" "$base/$tarball"
  curl -fsSL -o "$tmp/checksums.txt" "$base/caddy_${CADDY_VERSION}_checksums.txt"
  (cd "$tmp" && grep " $tarball\$" checksums.txt | sha512sum -c -)
  tar -xzf "$tmp/$tarball" -C "$tmp" caddy
  install -m 0755 "$tmp/caddy" "$RS/bin/caddy"
  rm -rf "$tmp"
fi
"$RS/bin/caddy" version

cat > "$RS/Caddyfile" <<EOF
{
	admin off
	auto_https off
}

:8188 {
	basic_auth {
		$USER_NAME $HASH
	}
	reverse_proxy 127.0.0.1:8189
}
EOF
chmod 600 "$RS/Caddyfile"
"$RS/bin/caddy" validate --config "$RS/Caddyfile" --adapter caddyfile

ARGS="$RS/comfyui_args.txt"
touch "$ARGS"
grep -qxF -- "--listen 127.0.0.1" "$ARGS" || echo "--listen 127.0.0.1" >> "$ARGS"
grep -qxF -- "--port 8189" "$ARGS" || echo "--port 8189" >> "$ARGS"

printf '#!/bin/sh\n# FileBrowser disabled (see boot.sh)\nexit 0\n' > "$RS/shims/filebrowser"
chmod +x "$RS/shims/filebrowser"

install -m 0755 "$HERE/boot.sh" "$RS/boot.sh"
echo "setup_auth: done"

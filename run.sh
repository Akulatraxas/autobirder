#!/usr/bin/env bash
# Build and run Autobirder Web locally (podman or docker).
#
# Usage: ./run.sh /path/to/photos
#
# Environment:
#   PORT              host port to publish (default: 8000)
#   CONTAINER_ENGINE  "podman" or "docker" (default: auto-detect, podman first)
#   RUN_AS_ROOT=1     run the container as root. Only for special setups where your own user
#                     cannot write to the photos (e.g. an SMB share mounted in WSL).
#
# Then open http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")"

if [ $# -ne 1 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
  echo "Usage: $0 /path/to/photos" >&2
  echo "WARNING: the photo folder is modified in place (tags are written to the files)." >&2
  exit 1
fi

photos_dir="$1"
if [ ! -d "$photos_dir" ]; then
  echo "Error: '$photos_dir' is not a directory" >&2
  exit 1
fi
photos_dir="$(cd "$photos_dir" && pwd)"

port="${PORT:-8000}"

engine="${CONTAINER_ENGINE:-}"
if [ -z "$engine" ]; then
  if command -v podman >/dev/null 2>&1; then
    engine=podman
  elif command -v docker >/dev/null 2>&1; then
    engine=docker
  else
    echo "Error: neither podman nor docker found" >&2
    exit 1
  fi
fi

# Run as your own user so written files keep your ownership.
# Rootless podman: keep-id maps your uid into the container.
# Docker: pass uid:gid directly.
if [ "${RUN_AS_ROOT:-0}" = "1" ]; then
  echo "WARNING: running the container as root (RUN_AS_ROOT=1)" >&2
  user_args=(--user 0:0)
elif [ "$engine" = "podman" ]; then
  user_args=(--userns=keep-id)
else
  user_args=(--user "$(id -u):$(id -g)")
fi

"$engine" build -t autobirder-web:latest .

# Published on 127.0.0.1 only: the app has no authentication.
"$engine" run --rm -it \
  "${user_args[@]}" \
  -p "127.0.0.1:${port}:8000" \
  -v "${photos_dir}":/data \
  -v autobirder_cache:/cache \
  -v bioclip_cache:/app/hf_cache \
  autobirder-web:latest

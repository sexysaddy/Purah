#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Keep the manual launcher on the same Wayland layer-shell surface; do not expose
# the old Qt top-level fallback that inherits normal window blur/3D transformations.
exec "$ROOT/start-purah.sh" --listen "$@"

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "[reset] Removing auth tokens and runtime data…"
rm -f data/auth.json || true
rm -f data/auth/auth.json || true

# Keep directories, just clear contents safely
mkdir -p data/auth data/runs data/thumb_cache

find data/runs -mindepth 1 -maxdepth 1 -type f -print -exec rm -f {} + || true
find data/thumb_cache -mindepth 1 -maxdepth 1 -type f -print -exec rm -f {} + || true

echo "[reset] Ensuring .gitkeep placeholders exist…"
touch data/.gitkeep data/auth/.gitkeep data/runs/.gitkeep data/thumb_cache/.gitkeep

echo "[reset] Done."


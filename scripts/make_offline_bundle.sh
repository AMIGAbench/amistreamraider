#!/usr/bin/env bash
set -euo pipefail

# Build an offline distribution bundle containing:
# - prebuilt Docker image tarball
# - docker-compose.yml (offline, uses local image tag)
# - README_OFFLINE.md with instructions
# - helper scripts (start.sh/stop.sh)

VER=${1:-0.1.0}
IMAGE_TAG=${2:-asrserver:${VER}}

ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
DIST_DIR="${ROOT_DIR}/dist/asrserver-offline-${VER}"
mkdir -p "${DIST_DIR}"

echo "[1/4] Building image ${IMAGE_TAG}…"
docker build -t "${IMAGE_TAG}" "${ROOT_DIR}"

echo "[2/4] Saving image to tar…"
docker save -o "${DIST_DIR}/asrserver-${VER}.tar" "${IMAGE_TAG}"

echo "[3/4] Preparing offline files…"
cp "${ROOT_DIR}/docker-compose.offline.yml" "${DIST_DIR}/docker-compose.yml"
cp "${ROOT_DIR}/docs/README_OFFLINE.md" "${DIST_DIR}/README_OFFLINE.md"
cat > "${DIST_DIR}/start.sh" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
IMG_TAR=${1:-asrserver-0.1.0.tar}
echo "Loading image from ${IMG_TAR}…"
docker load -i "${IMG_TAR}"
mkdir -p data/runs data/thumb_cache
docker compose up -d
echo "Service started. API: http://localhost:8080"
EOS
chmod +x "${DIST_DIR}/start.sh"

cat > "${DIST_DIR}/stop.sh" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
docker compose down
echo "Service stopped."
EOS
chmod +x "${DIST_DIR}/stop.sh"

echo "[4/4] Creating archive…"
(cd "${DIST_DIR}/.." && tar czf "asrserver-offline-${VER}.tar.gz" "asrserver-offline-${VER}")

echo "Done. Bundle at: ${DIST_DIR}/../asrserver-offline-${VER}.tar.gz"


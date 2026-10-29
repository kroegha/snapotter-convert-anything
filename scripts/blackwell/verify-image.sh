#!/usr/bin/env bash
# Self-check of a freshly built snapotter-local image (run by Build-Image.ps1).
set -euo pipefail
ls /opt/snapotter-wheels/ctranslate2-*.whl
python3 -I - <<'PY'
import json
with open("/app/docker/feature-manifest.json") as f:
    m = json.load(f)
print("bundleRepo", m["bundleRepo"], "| ocrBundleRepo", m["ocrBundleRepo"])
print("transcription amd64-gpu archive:", m["bundles"]["transcription"]["archives"]["amd64-gpu"]["file"])
PY
convert -list policy | grep -q "MVG,MSL,TEXT,TXT"
echo "ImageMagick policy hardened"
grep -q "enabled: false" /app/packages/shared/src/analytics/baked.ts && echo "analytics baked off"
echo "CUDA ${CUDA_VERSION:-?}"
ffmpeg -hide_banner -version | head -1
for e in h264_nvenc hevc_nvenc av1_nvenc libx264; do
  ffmpeg -hide_banner -encoders 2>/dev/null | grep -q " ${e} " || { echo "ffmpeg lacks ${e}" >&2; exit 1; }
done
echo "ffmpeg lists h264_nvenc hevc_nvenc av1_nvenc"
echo "NVIDIA_DRIVER_CAPABILITIES=${NVIDIA_DRIVER_CAPABILITIES:-?}"

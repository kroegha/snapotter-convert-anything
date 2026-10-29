#!/usr/bin/env bash
# Phase 0 spike, part C: run faster-whisper on the RTX 5090 with the
# source-built CTranslate2 wheel. Runs in the 13.4 runtime image, --gpus all.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_CACHE_DIR=/cache/pip
exec > >(tee /out/ct2-test.log) 2>&1

apt-get update -qq
apt-get install -y -qq --no-install-recommends python3 python3-venv ca-certificates >/dev/null
python3 -m venv /opt/venv; . /opt/venv/bin/activate
pip install -q /out/wheel/ctranslate2-*.whl
pip install -q "faster-whisper>=1.0.0" numpy==1.26.4 av==18.1.0
pip install -q --no-deps --force-reinstall /out/wheel/ctranslate2-*.whl

python3 - <<'PY'
import time, wave, struct, math, ctranslate2
from faster_whisper import WhisperModel
n = ctranslate2.get_cuda_device_count()
print({"ctranslate2": ctranslate2.__version__, "cuda_devices": n,
       "cuda_compute_types": sorted(ctranslate2.get_supported_compute_types("cuda")) if n else []})
assert n > 0, "source-built CTranslate2 sees no CUDA device"
# 20 s of synthetic audio: enough to exercise the encoder and decoder kernels.
with wave.open("/tmp/tone.wav", "w") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
    w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16000))) for i in range(16000 * 20)))
for ct in ("float16", "int8_float16", "bfloat16"):
    m = WhisperModel("Systran/faster-whisper-small", device="cuda", compute_type=ct)
    t = time.time(); segs, info = m.transcribe("/tmp/tone.wav", beam_size=5); list(segs)
    print(f"[PASS] faster-whisper small on cuda ({ct}): {time.time() - t:.2f}s, lang={info.language}")
PY
echo "== done"

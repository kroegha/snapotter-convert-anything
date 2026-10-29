# Phase 0 results — 29 Oct 2026

**Gate: GO.** Every GPU framework SnapOtter uses runs natively on the RTX 5090
(sm_120) on CUDA 13.4.

Host: MSI Raider, RTX 5090 Laptop GPU (24 GB, compute capability 12.0),
driver 617.42, Docker Desktop 4.95 (WSL2).

## Stack (part A) — `nvidia/cuda:13.4.3-cudnn-runtime-ubuntu24.04`

All seven amd64 GPU bundles' packages installed together in one venv under the
manifest's constraints (numpy 1.26.4 ABI), with:

| Package | Version |
|---|---|
| torch / torchvision | 2.15.0+cu134 / 0.29.2+cu134 (CUDA 13.4, cuDNN 9.26) |
| onnxruntime-gpu | 1.31.1 (CUDA 13.0 build) |
| numpy / scipy / opencv / Pillow | 1.26.4 / 1.12.0 / 4.10.0 / 13.0.0 |
| diffusers / transformers | 0.31.0 / 4.46.3 (unchanged pins) |
| rembg / mediapipe | 2.0.62 / 1.2.0 |

| Check | Result |
|---|---|
| NumPy-ABI stack imports | PASS |
| torch on CUDA (fp16/bf16 matmul, conv); `sm_120` in arch list | PASS |
| torchvision CUDA ops (nms) | PASS |
| ONNX Runtime CUDA EP, torch not imported | PASS — `CUDAExecutionProvider` active |
| SnapOtter `gpu.gpu_frameworks_available()` | PASS — torch, onnx, ctranslate2 all `True` |
| basicsr RRDBNet (Real-ESRGAN) fp16 on CUDA | PASS |
| LPIPS on CUDA | PASS |
| codeformer import | PASS |
| diffusers UNet + transformers CLIP on CUDA | PASS |
| rembg, mediapipe imports | PASS |

`pip check` gaps are the same ones upstream ships with: faster-whisper's CPU
`onnxruntime` (deliberately replaced by the GPU build) and codeformer-pip's
`gdown` (installed `--no-deps`).

## CTranslate2 (parts B, C)

- 4.8.4 built from source on `nvidia/cuda:13.4.3-cudnn-devel-ubuntu24.04`
  without patches. Arch flags passed as explicit `-gencode` to sidestep the
  FindCUDA two-digit parser.
- Wheel `ctranslate2-4.8.4-cp312-cp312-manylinux_2_39_x86_64.whl`, 29.8 MB.
  Embedded: **SASS 75, 80, 86, 89, 90, 120 + PTX 120**. The PyPI wheel has
  SASS up to 86 only.
- faster-whisper small on the 5090: float16, int8_float16 and bfloat16 all PASS.
- CPU backend is ruy rather than MKL. That only affects transcription if no GPU
  is present.

## Findings carried into Phase 1

1. **Pin `av==18.1.0` in the transcription bundle.** PyAV 19.0 removed the
   `metadata_errors` argument that faster-whisper 1.2.1's `decode_audio`
   passes, and SnapOtter's `transcribe.py` hands faster-whisper a file path.
   An unpinned rebuild today breaks transcription. This affects upstream too.
2. The torch line needs `--extra-index-url https://pypi.org/simple` alongside
   the cu134 index, so non-torch dependencies resolve.
3. The `onnxruntime==1.20.1` constraint moves to `1.31.1`.
4. Test-harness notes: no bundle installs scikit-learn or pandas despite the
   constraints listing them, and onnx newer than 1.16 needs a numpy-2-only
   `ml_dtypes`.

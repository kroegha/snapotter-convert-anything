# Phase 0 spike: native Blackwell (RTX 5090) on CUDA 13.4

Throwaway checks that decide the versions for the `blackwell-cuda13` branch.
Nothing here ships in the image.

| Script | Runs in | Proves |
|---|---|---|
| `stack-test.sh` → `install_stack.py`, `gpu_tests.py` | `nvidia/cuda:13.4.3-cudnn-runtime-ubuntu24.04`, `--gpus all` | Every amd64 GPU bundle's Python deps install together with torch 2.15.0+cu134 and onnxruntime-gpu 1.31.1 under the manifest's numpy 1.26.4 constraints, and each framework runs natively on sm_120 |
| `build-ct2.sh` | `nvidia/cuda:13.4.3-cudnn-devel-ubuntu24.04` | CTranslate2 4.8.4 builds against CUDA 13.4 with real sm_120 SASS |
| `ct2-test.sh` | runtime image, `--gpus all` | faster-whisper transcribes on the 5090 with that wheel |
| `fatbin_archs.py` | anywhere | Lists the GPU architectures embedded in a wheel or `.so` |

Results land in `out/` (`stack-test.log`, `install-report.json`, `gpu-report.json`,
`ct2-build.log`, `wheel/`, `ct2-test.log`).

## Run (PowerShell on the Raider, from the repo root)

```powershell
$R = "nvidia/cuda:13.4.3-cudnn-runtime-ubuntu24.04@sha256:3a6966a47d7dfe61350b721d5adbeca92a14eb77dffbb3769a06605c3768340e"
$D = "nvidia/cuda:13.4.3-cudnn-devel-ubuntu24.04@sha256:b3f64e4fec7d0825e05bf685e628e7e112afd7d63bc5906e2f71fd1f56cce336"
$S = "$PWD\spike\blackwell"
docker run -d --name snapotter-spike-a --gpus all --shm-size 4g -v "${S}:/spike" -v "${S}\out:/out" -v "$PWD\docker\feature-manifest.json:/spike/manifest.json:ro" -v "$PWD\packages\ai\python:/repo-ai:ro" -v snapotter-spike-pipcache:/cache $R bash /spike/stack-test.sh
docker run -d --name snapotter-spike-b -e CT2_JOBS=10 -v "${S}:/spike" -v "${S}\out:/out" -v snapotter-spike-pipcache:/cache $D bash /spike/build-ct2.sh
# after snapotter-spike-b exits 0:
docker run -d --name snapotter-spike-c --gpus all -v "${S}:/spike" -v "${S}\out:/out" -v snapotter-spike-pipcache:/cache $R bash /spike/ct2-test.sh
```

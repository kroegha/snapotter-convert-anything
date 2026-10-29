# Blackwell (CUDA 13.4) build runbook — MSI Raider

Builds the fork's CUDA 13.4 image, rebuilds the seven amd64 GPU AI bundles
against it, publishes them to the private Hugging Face repo, and runs the
result. Every command is PowerShell on the Raider, run from the repo root
`C:\Users\cfish\win-projects\snapotter-convert-anything` unless stated.

Build output lives outside the repo in
`C:\Users\cfish\win-projects\snapotter-build\` (`bundles\`, `logs\`).

## Prerequisites (checked 29 Oct 2026)

- NVIDIA driver R615 branch or newer (617.42 installed). The 13.4 base refuses
  to start on older drivers.
- Docker Desktop with the WSL2 backend and GPU support.
- Hugging Face write token at `%USERPROFILE%\.cache\huggingface\token`
  (user `kroegha`).
- ~150 GB free on the Docker disk.

## Phase 2 — build and publish

| Step | Command | Time |
|---|---|---|
| 1. Image | `.\scripts\blackwell\Build-Image.ps1` | 45-75 min first time |
| 2. Bundles | `.\scripts\blackwell\Build-Bundles.ps1` | 1.5-2.5 h |
| 3. Checksums + upload | `.\scripts\blackwell\Publish-Bundles.ps1` | ~1 h for ~30 GB up |
| 4. Commit manifest | `git add docker/feature-manifest.json; git commit -m "chore(ai): cu134-v1 bundle checksums"; git push` | — |
| 5. Rebuild image with the new checksums | `.\scripts\blackwell\Build-Image.ps1` | minutes (cached) |

Each script prints a final `... OK` line and writes a log under
`snapotter-build\logs\`. `Build-Bundles.ps1` skips bundles that already exist,
so re-running it after a failure resumes where it stopped. To rebuild one
bundle, delete its archive from `snapotter-build\bundles\` and run
`.\scripts\blackwell\Build-Bundles.ps1 -Bundles transcription`.

## Phase 3 — run and verify

One-time setup of `C:\Users\cfish\win-projects\snapotter\` (secrets in `.env`):

```powershell
New-Item -ItemType Directory -Force C:\Users\cfish\win-projects\snapotter | Out-Null
Copy-Item .\scripts\blackwell\compose.raider.yaml C:\Users\cfish\win-projects\snapotter\compose.yaml -Force
Set-Location C:\Users\cfish\win-projects\snapotter
function New-Secret { $b = New-Object byte[] 24; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); ($b | ForEach-Object { $_.ToString('x2') }) -join '' }
if (-not (Test-Path .env)) { "SNAPOTTER_ADMIN_PASSWORD=$(New-Secret)`nPOSTGRES_PASSWORD=$(New-Secret)`nPOSTGRES_APP_PASSWORD=$(New-Secret)`nREDIS_PASSWORD=$(New-Secret)" | Set-Content -Encoding ascii .env }
docker compose up -d
docker logs snapotter 2>&1 | Select-String GPU
Start-Process http://localhost:1349
```

Then run the automated verification (from the repo root):

```powershell
.\scripts\blackwell\Verify-Phase3.ps1 -Mode gpu -Setup -Install   # first run
.\scripts\blackwell\Verify-Phase3.ps1 -Mode cpu                   # same image, SNAPOTTER_GPU=0
.\scripts\blackwell\Verify-Phase3.ps1 -Mode gpu                   # back to GPU
```

`-Setup` signs in as `craig`, rotates the forced first-login password to a new
random one (written back into `.env` as `SNAPOTTER_ADMIN_PASSWORD`), and saves a
new API key to `api-key.txt` next to `.env`. `-Install` installs all seven AI
bundles from the private Hugging Face repo. Each run executes every GPU tool
twice (cold, warm) on the repo's fixtures, logs `nvidia-smi` every 250 ms, and
writes `snapotter-build\phase3\report.md` (GPU vs CPU timings, peak GPU
utilisation and VRAM per tool) plus the tool outputs.

Pass criteria (PRD §3):

1. The container starts with the GPU visible.
2. The AI sidecar reports `torch`, `onnx` and `ctranslate2` all `True`, and the
   logs show no "will run on CPU" warning (Verify-Phase3.ps1 prints both).
3. Each GPU tool completes, with GPU utilisation during its window.
4. `report.md` timing table: GPU against the CPU path on the same image.
5. Non-AI smoke test: image convert, video compress, PDF merge, OCR.

## Upstream drift

The fork's changes are confined to `docker/Dockerfile`,
`docker/feature-manifest.json`, `apps/api/src/routes/features.ts`
(`ocrBundleRepo`), the three security fixes, and this folder. To take an
upstream release: `git fetch upstream; git rebase upstream/main` on
`blackwell-cuda13`, resolve those files, then run Phase 2 again only if the
upstream release touched the AI bundles (`feature-manifest.json` packages)
or the Dockerfile base.

<#
.SYNOPSIS
  Build the fork's CUDA 13.4 SnapOtter image on the Raider.
.DESCRIPTION
  Run from anywhere; paths resolve from this script. Tags snapotter-local:cu134 (or -Image).
  First build pulls the 13.4 runtime + devel bases and compiles CTranslate2
  (~45-75 min); rebuilds after a manifest change reuse the cache.
  Log: <repo>\..\snapotter-build\logs\image-<timestamp>.log
#>
param(
  [string]$Image = "snapotter-local:cu134",
  [int]$Ct2Jobs = 12
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Build = Join-Path (Split-Path $Repo -Parent) "snapotter-build"
$Logs = Join-Path $Build "logs"
New-Item -ItemType Directory -Force $Logs | Out-Null
$Log = Join-Path $Logs ("image-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))

# Pre-fetch the FFmpeg release (phase 2): ffmpeg.org is intermittently
# unreachable from this network, so fetch it once with retries, check it against
# the Dockerfile's SHA-256 pin, cache it in snapotter-build\sources and hand it
# to the build as the ffmpeg-src context.
$Dockerfile = Join-Path $Repo "docker\Dockerfile"
$df = Get-Content $Dockerfile -Raw
$ffVer = [regex]::Match($df, 'ARG FFMPEG_VERSION=(\S+)').Groups[1].Value
$ffSha = [regex]::Match($df, 'ARG FFMPEG_SHA256=([0-9a-f]{64})').Groups[1].Value
if (-not $ffVer -or -not $ffSha) { throw "could not read the FFmpeg pin from $Dockerfile" }
$Sources = Join-Path $Build "sources"
New-Item -ItemType Directory -Force $Sources | Out-Null
$tarball = Join-Path $Sources "ffmpeg-$ffVer.tar.xz"
for ($i = 1; $i -le 8; $i++) {
  if ((Test-Path $tarball) -and ((Get-FileHash $tarball -Algorithm SHA256).Hash.ToLower() -eq $ffSha)) { break }
  Remove-Item $tarball -ErrorAction SilentlyContinue
  Write-Host "Fetching ffmpeg-$ffVer.tar.xz (attempt $i)"
  try {
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -Uri "https://ffmpeg.org/releases/ffmpeg-$ffVer.tar.xz" -OutFile $tarball -UseBasicParsing -TimeoutSec 300
  } catch { Write-Host "  failed: $($_.Exception.Message)"; Start-Sleep 20 }
}
if (-not ((Test-Path $tarball) -and ((Get-FileHash $tarball -Algorithm SHA256).Hash.ToLower() -eq $ffSha))) {
  throw "could not fetch ffmpeg-$ffVer.tar.xz matching the pinned SHA-256"
}

# Accurate OCR (Balanced/Best) downloads upstream's Ed25519-signed OCR runtime
# from deepsafe/feature-bundles and only runs in an "official" container that
# pins upstream's public release key. Key id and public key (public data, no
# secret) taken from the official image ghcr.io/snapotter-hq/snapotter:2.2.0
# (/app/docker/ocr-runtime-trust.json) and checked against
# v2.2.0/v3/ocr-runtime-trusted-keys.json on Hugging Face on 30 Oct 2026; both
# matched. If upstream rotates the key, refresh these two values.
$OcrTrustId = "snapotter-ocr-2026-07"
$OcrTrustPem = "-----BEGIN PUBLIC KEY-----`nMCowBQYDK2VwAyEA8wzT/8aMnaixlLhGcvZwywb3UeOcJUeBnvVPDrx19L8=`n-----END PUBLIC KEY-----`n"
$OcrTrustPemB64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($OcrTrustPem))
$OcrArgs = "--build-arg SNAPOTTER_OFFICIAL_CONTAINER=1 --build-arg OCR_RUNTIME_TRUST_ID=$OcrTrustId --build-arg OCR_RUNTIME_TRUST_PEM_B64=$OcrTrustPemB64"

Write-Host "Building $Image from $Repo (log: $Log)"
$env:DOCKER_BUILDKIT = "1"
# Redirect in cmd, not through the PowerShell pipeline: Windows PowerShell
# holds a native command's piped output until it exits, so the log would stay
# empty for the whole build. Follow it with: Get-Content <log> -Wait -Tail 20
cmd /c "docker build --platform linux/amd64 --file `"$Dockerfile`" --build-arg SNAPOTTER_ANALYTICS=off --build-arg CT2_JOBS=$Ct2Jobs $OcrArgs --build-context ffmpeg-src=`"$Sources`" --progress plain --tag $Image `"$Repo`" > `"$Log`" 2>&1"
if ($LASTEXITCODE -ne 0) { Get-Content $Log -Tail 30; throw "docker build failed ($LASTEXITCODE); see $Log" }

Write-Host "== Verifying the image"
$Verify = Join-Path $PSScriptRoot "verify-image.sh"
cmd /c "docker run --rm --entrypoint bash -v `"${Verify}:/verify-image.sh:ro`" $Image /verify-image.sh >> `"$Log`" 2>&1"
if ($LASTEXITCODE -ne 0) { Get-Content $Log -Tail 20; throw "image verification failed; see $Log" }
Get-Content $Log -Tail 6
Write-Host "IMAGE BUILD OK: $Image"

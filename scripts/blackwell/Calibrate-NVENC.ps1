<#
.SYNOPSIS
  Phase 2: build the test clips and run the NVENC quality calibration.
.DESCRIPTION
  1. Copies the upstream static ffmpeg (it has libvmaf; Ubuntu 24.04 packages
     no libvmaf) to snapotter-build\tools\ffmpeg-vmaf.
  2. Builds phone.mp4, long4k.mp4 and screen.mp4 in snapotter-build\clips
     (skipped when all three exist).
  3. Runs calibrate_nvenc.py in the NVENC image with the GPU. It resumes from
     calibration-<codec>.json, so a re-run continues where it stopped.
  Output: snapotter-build\phase3\calibration\calibration-<codec>.{json,md}
  Long-running (1-3 h): start it detached and follow the log.
#>
param(
  [string]$Image = "snapotter-local:cu134",
  # Ubuntu 24.04 packages no libvmaf, so VMAF comes from the pinned upstream
  # static FFmpeg image (the arm64 FFmpeg stage in docker/Dockerfile).
  [string]$VmafImage = "mwader/static-ffmpeg:8.1.2@sha256:33f770f812cbfc3de96c547157fc9faf8bd95a36481753439ffa761045167585",
  [string]$VmafPath = "/ffmpeg",
  [ValidateSet("h264", "hevc")][string]$Codec = "h264"
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Build = Join-Path (Split-Path $Repo -Parent) "snapotter-build"
$Tools = Join-Path $Build "tools"; $Clips = Join-Path $Build "clips"; $Out = Join-Path $Build "phase3\calibration"
New-Item -ItemType Directory -Force $Tools, $Clips, $Out | Out-Null
$Gpu = "--gpus all -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video"

if (-not (Test-Path "$Tools\ffmpeg-vmaf")) {
  cmd /c "docker rm -f vmaf-src >nul 2>&1"
  cmd /c "docker create --name vmaf-src $VmafImage >nul && docker cp vmaf-src:$VmafPath `"$Tools\ffmpeg-vmaf`" && docker rm vmaf-src >nul"
  if ($LASTEXITCODE -ne 0) { throw "could not copy the VMAF ffmpeg out of $VmafImage" }
}

if (-not ((Test-Path "$Clips\phone.mp4") -and (Test-Path "$Clips\long4k.mp4") -and (Test-Path "$Clips\screen.mp4"))) {
  Write-Host "Building test clips (log: $Clips\make_clips.log)"
  cmd /c "docker run --rm $Gpu --entrypoint sh -v `"${Repo}:/repo:ro`" -v `"${Clips}:/clips`" $Image /repo/scripts/blackwell/make_clips.sh /repo/tests/fixtures /repo /clips > `"$Clips\make_clips.log`" 2>&1"
  if ($LASTEXITCODE -ne 0) { Get-Content "$Clips\make_clips.log" -Tail 20; throw "make_clips.sh failed" }
  Get-Content "$Clips\make_clips.log"
}

Write-Host "Calibrating $Codec (log: $Out\calibrate-$Codec.log)"
cmd /c "docker run --rm $Gpu --entrypoint python3 -v `"${Repo}:/repo:ro`" -v `"${Clips}:/clips:ro`" -v `"${Tools}:/tools:ro`" -v `"${Out}:/out`" $Image -I /repo/scripts/blackwell/calibrate_nvenc.py --clips /clips --out /out --vmaf /tools/ffmpeg-vmaf --codec $Codec > `"$Out\calibrate-$Codec.log`" 2>&1"
if ($LASTEXITCODE -ne 0) { Get-Content "$Out\calibrate-$Codec.log" -Tail 20; throw "calibration failed" }
Get-Content "$Out\calibration-$Codec.md"
Write-Host "CALIBRATION DONE: $Out\calibration-$Codec.md"

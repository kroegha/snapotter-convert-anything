<#
.SYNOPSIS
  Build the fork's CUDA 13.4 SnapOtter image on the Raider.
.DESCRIPTION
  Run from anywhere; paths resolve from this script. Tags snapotter-local:cu134.
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

Write-Host "Building $Image from $Repo (log: $Log)"
$env:DOCKER_BUILDKIT = "1"
# Redirect in cmd, not through the PowerShell pipeline: Windows PowerShell
# holds a native command's piped output until it exits, so the log would stay
# empty for the whole build. Follow it with: Get-Content <log> -Wait -Tail 20
$Dockerfile = Join-Path $Repo "docker\Dockerfile"
cmd /c "docker build --platform linux/amd64 --file `"$Dockerfile`" --build-arg SNAPOTTER_ANALYTICS=off --build-arg CT2_JOBS=$Ct2Jobs --progress plain --tag $Image `"$Repo`" > `"$Log`" 2>&1"
if ($LASTEXITCODE -ne 0) { Get-Content $Log -Tail 30; throw "docker build failed ($LASTEXITCODE); see $Log" }

Write-Host "== Verifying the image"
$Verify = Join-Path $PSScriptRoot "verify-image.sh"
cmd /c "docker run --rm --entrypoint bash -v `"${Verify}:/verify-image.sh:ro`" $Image /verify-image.sh >> `"$Log`" 2>&1"
if ($LASTEXITCODE -ne 0) { Get-Content $Log -Tail 20; throw "image verification failed; see $Log" }
Get-Content $Log -Tail 6
Write-Host "IMAGE BUILD OK: $Image"

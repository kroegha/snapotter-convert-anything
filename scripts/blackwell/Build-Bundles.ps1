<#
.SYNOPSIS
  Build the amd64-gpu AI bundles inside the snapotter-local image.
.DESCRIPTION
  Each bundle builds in a fresh container (build-bundle.sh diffs against the
  image's clean base venv, so containers must not be reused). Bundles already
  present in the output folder are skipped, so the script can be re-run after a
  failure. Output: <repo>\..\snapotter-build\bundles\<id>-amd64-gpu.tar.gz
  Per-bundle logs: <repo>\..\snapotter-build\logs\bundle-<id>.log
  Needs network (pip, model downloads); ~1.5-2.5 h for all seven.
#>
param(
  [string]$Image = "snapotter-local:cu134",
  [string[]]$Bundles = @(
    "background-removal", "face-detection", "object-eraser-colorize",
    "inpaint-hq", "upscale-enhance", "photo-restoration", "transcription"
  )
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Build = Join-Path (Split-Path $Repo -Parent) "snapotter-build"
$Out = Join-Path $Build "bundles"
$Logs = Join-Path $Build "logs"
New-Item -ItemType Directory -Force $Out, $Logs | Out-Null
$BuildScript = Join-Path $Repo "docker\build-bundle.sh"

$failed = @()
foreach ($id in $Bundles) {
  $archive = Join-Path $Out "$id-amd64-gpu.tar.gz"
  if (Test-Path $archive) { Write-Host "= $id already built"; continue }
  $log = Join-Path $Logs "bundle-$id.log"
  $started = Get-Date
  Write-Host ("> {0} started {1:HH:mm}" -f $id, $started)
  # cmd redirection so the log fills live (see Build-Image.ps1).
  cmd /c "docker run --rm --name snapotter-bundle-$id --entrypoint bash -v `"${BuildScript}:/build-bundle.sh:ro`" -v `"${Out}:/output`" $Image /build-bundle.sh $id amd64-gpu /output > `"$log`" 2>&1"
  $mins = [math]::Round(((Get-Date) - $started).TotalMinutes, 1)
  if ($LASTEXITCODE -eq 0 -and (Test-Path $archive)) {
    $gb = [math]::Round((Get-Item $archive).Length / 1GB, 2)
    Write-Host "  OK $id in $mins min ($gb GB)"
  } else {
    Write-Host "  FAILED $id after $mins min; see $log"
    $failed += $id
  }
}
if ($failed.Count) { Write-Host "BUNDLES FAILED: $($failed -join ', ')"; exit 1 }
Write-Host "BUNDLES OK: $($Bundles -join ', ')"

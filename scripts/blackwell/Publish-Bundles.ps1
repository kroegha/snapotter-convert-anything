<#
.SYNOPSIS
  Write the built bundles' SHA-256/sizes into the manifest, then upload them.
.DESCRIPTION
  1. update_manifest.py rewrites docker\feature-manifest.json (amd64-gpu entries
     -> cu134-v1/<id>-amd64-gpu-r1.tar.gz) and writes upload-plan.json.
  2. publish_bundles.py uploads to the private bundleRepo
     (kroegha/snapotter-bundles-cu134), skipping files already there.
  The Hugging Face token is read from %USERPROFILE%\.cache\huggingface\token
  and mounted read-only; it never appears on a command line or in a log.
  Afterwards: commit the manifest and run Build-Image.ps1 again so the image
  carries the new checksums.
#>
param(
  [string]$Image = "snapotter-local:cu134",
  [string]$Release = "cu134-v1",
  [switch]$SkipManifest,
  [switch]$SkipUpload
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Build = Join-Path (Split-Path $Repo -Parent) "snapotter-build"
$Out = Join-Path $Build "bundles"
$Logs = Join-Path $Build "logs"
$Token = Join-Path $env:USERPROFILE ".cache\huggingface\token"

# Docker output goes through cmd redirection, never the PowerShell pipeline:
# Windows PowerShell turns a native command's stderr into a terminating error
# under $ErrorActionPreference = "Stop" (and buffers piped output).
$stamp = "{0:yyyyMMdd-HHmmss}" -f (Get-Date)
if (-not $SkipManifest) {
  $mlog = Join-Path $Logs "manifest-$stamp.log"
  Write-Host "Updating manifest checksums (log: $mlog)"
  cmd /c "docker run --rm --entrypoint python3 -v `"${Repo}:/repo`" -v `"${Out}:/bundles`" $Image -I /repo/scripts/blackwell/update_manifest.py /repo/docker/feature-manifest.json /bundles $Release > `"$mlog`" 2>&1"
  if ($LASTEXITCODE -ne 0) { Get-Content $mlog -Tail 20; throw "manifest update failed; see $mlog" }
  Get-Content $mlog
}

if ($SkipUpload) { Write-Host "Manifest updated; upload skipped."; exit 0 }
if (-not (Test-Path $Token)) { throw "No Hugging Face token at $Token" }
if (-not (Test-Path (Join-Path $Out "upload-plan.json"))) { throw "No upload-plan.json; run without -SkipManifest first" }

$log = Join-Path $Logs "publish-$stamp.log"
Write-Host "Uploading (log: $log)"
cmd /c "docker run --rm --entrypoint python3 -e HF_TOKEN_PATH=/run/secrets/hf_token -v `"${Token}:/run/secrets/hf_token:ro`" -v `"${Repo}:/repo:ro`" -v `"${Out}:/bundles:ro`" $Image -I /repo/scripts/blackwell/publish_bundles.py /bundles/upload-plan.json > `"$log`" 2>&1"
if ($LASTEXITCODE -ne 0) { Get-Content $log -Tail 20; throw "upload failed; see $log" }
Get-Content $log -Tail 12
Write-Host "PUBLISH OK"

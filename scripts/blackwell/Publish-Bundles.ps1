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
  [switch]$SkipUpload
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Build = Join-Path (Split-Path $Repo -Parent) "snapotter-build"
$Out = Join-Path $Build "bundles"
$Logs = Join-Path $Build "logs"
$Token = Join-Path $env:USERPROFILE ".cache\huggingface\token"

& docker run --rm --entrypoint python3 `
  -v "${Repo}:/repo" -v "${Out}:/bundles" `
  $Image -I /repo/scripts/blackwell/update_manifest.py /repo/docker/feature-manifest.json /bundles $Release
if ($LASTEXITCODE -ne 0) { throw "manifest update failed" }

# The plan was written with container paths; nothing to translate.
if ($SkipUpload) { Write-Host "Manifest updated; upload skipped."; exit 0 }
if (-not (Test-Path $Token)) { throw "No Hugging Face token at $Token" }

$log = Join-Path $Logs ("publish-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))
& docker run --rm --entrypoint python3 `
  -e HF_TOKEN_PATH=/run/secrets/hf_token `
  -v "${Token}:/run/secrets/hf_token:ro" `
  -v "${Repo}:/repo:ro" -v "${Out}:/bundles:ro" `
  $Image -I /repo/scripts/blackwell/publish_bundles.py /bundles/upload-plan.json *>&1 | Tee-Object -FilePath $log
if ($LASTEXITCODE -ne 0) { throw "upload failed; see $log" }
Write-Host "PUBLISH OK"

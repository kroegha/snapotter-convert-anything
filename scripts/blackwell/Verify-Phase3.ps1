<#
.SYNOPSIS
  Phase 3: run the AI tool matrix against the local stack on GPU or CPU.
.DESCRIPTION
  Uses the compose project in C:\Users\cfish\win-projects\snapotter (see
  README.md). -Mode gpu runs normally; -Mode cpu recreates the app container with
  SNAPOTTER_GPU=0 so the same image times the CPU path. -Setup (first run only)
  rotates the first-login password into .env and creates api-key.txt; -Install
  installs all AI bundles from the private Hugging Face repo first.
  Output: <repo>\..\snapotter-build\phase3\<mode>\ (results.json, outputs,
  nvidia-smi.csv) and phase3\report.md.
#>
param(
  [ValidateSet("gpu", "cpu", "smoke")][string]$Mode = "gpu",
  [switch]$Setup,
  [switch]$Install,
  [string]$StackDir = "C:\Users\cfish\win-projects\snapotter",
  [string]$Image = "snapotter-local:cu134"
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Phase3 = Join-Path (Split-Path $Repo -Parent) "snapotter-build\phase3"
$Out = Join-Path $Phase3 $Mode
New-Item -ItemType Directory -Force $Out | Out-Null
$Log = Join-Path $Out "verify.log"

Push-Location $StackDir
try {
  $env:SNAPOTTER_GPU = if ($Mode -eq "cpu") { "0" } else { "auto" }
  cmd /c "docker compose up -d > `"$Out\compose.log`" 2>&1"
  if ($LASTEXITCODE -ne 0) { Get-Content "$Out\compose.log"; throw "docker compose up failed" }
} finally { Pop-Location }

Write-Host "Waiting for snapotter to report healthy..."
for ($i = 0; $i -lt 60; $i++) {
  $h = (cmd /c "docker inspect -f {{.State.Health.Status}} snapotter 2>nul").Trim()
  if ($h -eq "healthy") { break }
  Start-Sleep 5
}
if ($h -ne "healthy") { throw "snapotter did not become healthy (last: $h)" }

$smiCsv = Join-Path $Out "nvidia-smi.csv"
$smi = $null
if ($Mode -eq "gpu") {
  $smi = Start-Process nvidia-smi -ArgumentList "--query-gpu=timestamp,utilization.gpu,memory.used", "--format=csv,noheader,nounits", "-lms", "250", "-f", "`"$smiCsv`"" -WindowStyle Hidden -PassThru
}

$toolArgs = "--out /out/$Mode"
if ($Setup) { $toolArgs += " --setup" }
if ($Install) { $toolArgs += " --install" }
if ($Mode -eq "smoke") { $toolArgs += " --smoke" }
Write-Host "Running tool matrix ($Mode); log: $Log"
cmd /c "docker run --rm --network snapotter_default --entrypoint /opt/venv/bin/python -v `"${Repo}:/repo:ro`" -v `"${StackDir}:/secrets`" -v `"${Phase3}:/out`" $Image -I /repo/scripts/blackwell/verify_tools.py --base http://snapotter:1349 --env /secrets/.env $toolArgs > `"$Log`" 2>&1"
$matrixExit = $LASTEXITCODE
if ($smi) { Stop-Process -Id $smi.Id -ErrorAction SilentlyContinue }
Get-Content $Log -Tail 30

if ($Mode -eq "gpu") {
  Write-Host "== GPU frameworks seen by the AI sidecar"
  cmd /c "docker exec snapotter /data/ai/venv/bin/python -c `"import sys; sys.path.insert(0,'/app/packages/ai/python'); import gpu; print(gpu.gpu_frameworks_available())`" 2>&1"
  cmd /c "docker logs snapotter 2>&1" | Select-String -Pattern "will run on CPU|no kernels for it" | Select-Object -First 5
}

if ($Mode -eq "smoke") {
  if ($matrixExit -ne 0) { throw "smoke test exited $matrixExit; see $Log" }
  Write-Host "PHASE3 SMOKE DONE: $Out\smoke.json"; exit 0
}
$tz = [System.TimeZoneInfo]::Local.GetUtcOffset((Get-Date)).TotalHours
cmd /c "docker run --rm --entrypoint python3 -v `"${Repo}:/repo:ro`" -v `"${Phase3}:/out`" $Image -I /repo/scripts/blackwell/gpu_report.py /out $tz 2>&1"
if ($matrixExit -ne 0) { throw "tool matrix exited $matrixExit; see $Log" }
Write-Host "PHASE3 $($Mode.ToUpper()) DONE: $Phase3\report.md"

<#
.SYNOPSIS
  Phase 2 (NVENC) verification: every H.264 tool on NVENC, then on software.
.DESCRIPTION
  Runs the stack in C:\Users\cfish\win-projects\snapotter on -Image (default
  the NVENC image) twice: with SNAPOTTER_HW_ACCEL=nvenc, then with it empty
  (software), each through verify_tools.py --video on the test clips built by
  Calibrate-NVENC.ps1. Logs nvidia-smi (incl. encoder utilisation) during the
  NVENC run and writes snapotter-build\phase3\video\report.md comparing the two.
  Leaves the stack running on the NVENC image with NVENC on.
  Long-running (the 10-minute 4K and screen clips on software dominate):
  start it detached and follow snapotter-build\phase3\video\<tag>\verify.log.
#>
param(
  [string]$Image = "snapotter-local:cu134",
  [string]$StackDir = "C:\Users\cfish\win-projects\snapotter",
  [ValidateSet("both", "nvenc", "software")][string]$Runs = "both"
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Build = Join-Path (Split-Path $Repo -Parent) "snapotter-build"
$Root = Join-Path $Build "phase3\video"
$Clips = Join-Path $Build "clips"; $Tools = Join-Path $Build "tools"
foreach ($f in "phone.mp4", "long4k.mp4", "screen.mp4") {
  if (-not (Test-Path "$Clips\$f")) { throw "missing $Clips\$f; run Calibrate-NVENC.ps1 first" }
}
if (-not (Test-Path "$Tools\ffmpeg-vmaf")) { throw "missing $Tools\ffmpeg-vmaf; run Calibrate-NVENC.ps1 first" }
Copy-Item (Join-Path $PSScriptRoot "compose.raider.yaml") (Join-Path $StackDir "compose.yaml") -Force

function Start-Stack([string]$accel, [string]$out) {
  $env:SNAPOTTER_IMAGE = $Image
  $env:SNAPOTTER_GPU = "auto"
  # PowerShell deletes a variable set to "", so software mode is an override file.
  $files = "-f compose.yaml"
  if ($accel -eq "software") {
    "services:`n  snapotter:`n    environment:`n      SNAPOTTER_HW_ACCEL: `"`"`n" | Set-Content -Encoding ascii (Join-Path $StackDir "compose.software.yaml")
    $files += " -f compose.software.yaml"
  }
  Push-Location $StackDir
  try {
    cmd /c "docker compose $files up -d --force-recreate snapotter > `"$out\compose.log`" 2>&1"
    if ($LASTEXITCODE -ne 0) { Get-Content "$out\compose.log"; throw "docker compose up failed" }
  } finally { Pop-Location }
  for ($i = 0; $i -lt 60; $i++) {
    $h = (cmd /c "docker inspect -f {{.State.Health.Status}} snapotter 2>nul").Trim()
    if ($h -eq "healthy") { break }
    Start-Sleep 5
  }
  if ($h -ne "healthy") { throw "snapotter did not become healthy (last: $h)" }
  cmd /c "docker logs snapotter 2>&1" | Select-String -Pattern "Hardware encoders|SNAPOTTER_HW_ACCEL|nvenc" |
    Select-Object -First 5 | ForEach-Object { $_.Line } | Tee-Object -FilePath "$out\hwaccel-log.txt"
}

$tags = if ($Runs -eq "both") { @("nvenc", "software") } else { @($Runs) }
foreach ($tag in $tags) {
  $out = Join-Path $Root $tag
  New-Item -ItemType Directory -Force $out | Out-Null
  Write-Host "== $tag run: stack on $Image"
  Start-Stack $tag $out
  $smi = $null
  if ($tag -eq "nvenc") {
    $smi = Start-Process nvidia-smi -ArgumentList "--query-gpu=timestamp,utilization.gpu,utilization.encoder,memory.used", "--format=csv,noheader,nounits", "-lms", "500", "-f", "`"$out\nvidia-smi.csv`"" -WindowStyle Hidden -PassThru
  }
  cmd /c "docker run --rm --network snapotter_default --entrypoint /opt/venv/bin/python -v `"${Repo}:/repo:ro`" -v `"${StackDir}:/secrets`" -v `"${Clips}:/clips:ro`" -v `"${Tools}:/tools:ro`" -v `"${out}:/out`" $Image -I /repo/scripts/blackwell/verify_tools.py --base http://snapotter:1349 --env /secrets/.env --out /out --video $tag --vmaf /tools/ffmpeg-vmaf > `"$out\verify.log`" 2>&1"
  $exit = $LASTEXITCODE
  if ($smi) { Stop-Process -Id $smi.Id -ErrorAction SilentlyContinue }
  Get-Content "$out\verify.log" -Tail 25
  if ($exit -ne 0) { Write-Host "!! $tag run had failures (exit $exit)" }
}

if ($Runs -ne "nvenc") {
  Write-Host "== restoring NVENC"
  Start-Stack "nvenc" (Join-Path $Root "nvenc")
}
Remove-Item (Join-Path $StackDir "compose.software.yaml") -ErrorAction SilentlyContinue
$tz = [System.TimeZoneInfo]::Local.GetUtcOffset((Get-Date)).TotalHours
cmd /c "docker run --rm --entrypoint python3 -v `"${Repo}:/repo:ro`" -v `"${Root}:/out`" $Image -I /repo/scripts/blackwell/video_report.py /out $tz 2>&1"
Write-Host "VIDEO VERIFY DONE: $Root\report.md"

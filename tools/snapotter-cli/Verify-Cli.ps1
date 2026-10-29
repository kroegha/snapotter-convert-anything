<#
.SYNOPSIS
  Live checks for the installed snapotter CLI against the running stack
  (CLI PRD criteria 3-6).
.DESCRIPTION
  Run in PowerShell from the repo root:
    .\tools\snapotter-cli\Verify-Cli.ps1
  Copies fixtures to C:\Users\cfish\win-projects\snapotter-build\cli-check\
  (outputs land there, never in the repo), then runs one tool per section,
  one AI tool per framework, the error paths, the no-overwrite rule and a
  detach/wait round trip. Writes results.json there and exits non-zero on any
  failed check.
#>
param([string]$Work = "C:\Users\cfish\win-projects\snapotter-build\cli-check")
$ErrorActionPreference = "Stop"
$Fix = Join-Path $PSScriptRoot "..\..\tests\fixtures"
$cli = Join-Path $env:USERPROFILE ".local\bin\snapotter.cmd"
if (Test-Path $Work) { Get-ChildItem $Work -File | Remove-Item -Force }
New-Item -ItemType Directory -Force $Work | Out-Null
foreach ($f in "image\valid\portrait-color.jpg", "image\valid\test-200x150.png", "video\valid\hero.mov",
               "audio\valid\speech-10s.wav", "document\valid\test-3page.pdf", "document\valid\alt-2page.pdf") {
  Copy-Item (Join-Path $Fix $f) $Work -Force
}
Set-Content -Encoding ascii (Join-Path $Work "broken.jpg") "this is not an image"
Set-Location $Work
$results = @()

function Invoke-Cli([string]$label, [int]$expectExit, [string]$cliArgs, [scriptblock]$check = $null) {
  $start = Get-Date
  $raw = cmd /c "`"$cli`" $cliArgs --json --quiet 2>&1"
  $exit = $LASTEXITCODE
  $secs = [math]::Round(((Get-Date) - $start).TotalSeconds, 1)
  $json = $raw | Where-Object { $_ -match '^\{' } | Select-Object -Last 1
  $rec = if ($json) { $json | ConvertFrom-Json } else { $null }
  $ok = ($exit -eq $expectExit)
  $note = ""
  if ($ok -and $check) {
    try { $note = & $check $rec } catch { $ok = $false; $note = "check failed: $($_.Exception.Message)" }
  }
  if (-not $ok -and -not $note) { $note = "exit $exit (expected $expectExit): $(($raw | Select-Object -Last 2) -join ' | ')" }
  $script:results += [pscustomobject]@{ check = $label; ok = $ok; exit = $exit; seconds = $secs; note = $note }
  Write-Host ("{0} {1,-46} exit {2} {3,6}s  {4}" -f ($(if ($ok) { "[OK ]" } else { "[ERR]" })), $label, $exit, $secs, $note)
  return $rec
}
function Assert-Output($rec) {
  if (-not $rec.output -or -not (Test-Path $rec.output)) { throw "no output file" }
  "$(Split-Path $rec.output -Leaf) $([math]::Round((Get-Item $rec.output).Length / 1KB)) KB"
}

# Criterion 3: one tool per section, one AI tool per framework
Invoke-Cli "image: convert jpg->webp" 0 "run convert portrait-color.jpg --set format=webp" ${function:Assert-Output} | Out-Null
Invoke-Cli "video: compress (NVENC)" 0 "run compress-video hero.mov --set quality=strong" ${function:Assert-Output} | Out-Null
Invoke-Cli "audio: convert wav->mp3" 0 "run convert-audio speech-10s.wav --set format=mp3" ${function:Assert-Output} | Out-Null
Invoke-Cli "pdf: merge two PDFs" 0 "run merge-pdf test-3page.pdf alt-2page.pdf" ${function:Assert-Output} | Out-Null
Invoke-Cli "files: zip two files" 0 "run create-zip test-200x150.png speech-10s.wav" ${function:Assert-Output} | Out-Null
Invoke-Cli "AI onnx: remove background" 0 "run remove-background portrait-color.jpg" ${function:Assert-Output} | Out-Null
Invoke-Cli "AI torch: upscale x2" 0 "run upscale test-200x150.png --set scale=2" ${function:Assert-Output} | Out-Null
Invoke-Cli "AI ctranslate2: transcribe" 0 "run transcribe-audio speech-10s.wav --set language=en" {
  param($r) $o = Assert-Output $r; $text = (Get-Content $r.output -Raw).Trim()
  if ($text.Length -lt 5) { throw "empty transcript" }; "${o}: '$($text.Substring(0, [Math]::Min(50, $text.Length)))'"
} | Out-Null
Invoke-Cli "generator: QR code" 0 "run qr-generate --set text=snapotter-cli --out qr.png" ${function:Assert-Output} | Out-Null

# Criterion 4: errors before upload, and server errors with the server's message
Invoke-Cli "unknown tool -> exit 2" 2 "run compres-video hero.mov" { param($r) if ($r.error -notmatch "compress-video") { throw "no suggestion" }; "suggests compress-video" } | Out-Null
Invoke-Cli "bad choice -> exit 2" 2 "run compress-video hero.mov --set quality=max" { param($r) if ($r.error -notmatch "light, balanced, strong") { throw "no choices" }; "lists the choices" } | Out-Null
Invoke-Cli "missing required -> exit 2" 2 "run convert portrait-color.jpg" { param($r) if ($r.error -notmatch "format") { throw "no hint" }; "names 'format'" } | Out-Null
Invoke-Cli "broken input -> exit 3 or 4" 3 "run remove-background broken.jpg" { param($r) "server said: $($r.error)" } | Out-Null
if (-not $results[-1].ok -and $results[-1].exit -eq 4) { $results[-1].ok = $true; $results[-1].note = "job failed with the server's message" }

# Criterion 5: no overwrite without --force
$first = Invoke-Cli "no-overwrite: first run" 0 "run convert test-200x150.png --set format=jpg" ${function:Assert-Output}
$second = Invoke-Cli "no-overwrite: second run" 0 "run convert test-200x150.png --set format=jpg" {
  param($r) if ($r.output -notmatch '\(1\)') { throw "did not pick a new name: $($r.output)" }; Split-Path $r.output -Leaf
}
Invoke-Cli "--force replaces" 0 "run convert test-200x150.png --set format=jpg --force" {
  param($r) if ($r.output -match '\(1\)') { throw "did not overwrite" }; Split-Path $r.output -Leaf
} | Out-Null

# Long-job pattern for cloud sessions: detach, then wait
$det = Invoke-Cli "detach compress" 0 "run compress-video hero.mov --detach" { param($r) if (-not $r.jobId) { throw "no job id" }; "job $($r.jobId)" }
if ($det.jobId) {
  Invoke-Cli "wait for detached job" 0 "wait $($det.jobId) --out $Work\" ${function:Assert-Output} | Out-Null
}

# Criterion 6 (part): the key never shows up in any output
$key = [IO.File]::ReadAllText("$env:USERPROFILE\.config\snapotter\key").Trim()
$leak = Get-ChildItem $Work -File | Where-Object { $_.Length -lt 5MB } | Select-String -SimpleMatch $key -List
$results += [pscustomobject]@{ check = "key never written to outputs"; ok = (-not $leak); exit = 0; seconds = 0; note = "" }
$key = $null

$results | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $Work "results.json")
$bad = @($results | Where-Object { -not $_.ok })
"CLI CHECK: $($results.Count - $bad.Count)/$($results.Count) OK"
if ($bad.Count) { exit 1 }

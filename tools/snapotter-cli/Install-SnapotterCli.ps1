<#
.SYNOPSIS
  Install the snapotter CLI on the Raider and give it a scoped API key.
.DESCRIPTION
  Run in PowerShell from the repo root
  (C:\Users\cfish\win-projects\snapotter-convert-anything):

    .\tools\snapotter-cli\Install-SnapotterCli.ps1

  1. Copies snapotter.py and tools.json to %USERPROFILE%\.local\bin\snapotter\
     and writes the snapotter.cmd shim beside them, in a folder already on PATH.
  2. Unless a key exists (or -RotateKey is given): signs in as the stack's admin
     with the password from the stack's .env, creates an API key that may only
     run tools (tools:use) and expires in 12 months, and stores it in
     %USERPROFILE%\.config\snapotter\key, readable only by you. The key is
     never printed.
  3. Proves the key: a tool runs, and an admin call is refused.
  4. Revokes the full-permission testing key "claude-local" and removes the
     api-key.txt that held it (-KeepTestingKey skips this).
  Re-run after an upgrade to refresh the CLI and catalogue; the key is kept.
#>
param(
  [string]$StackDir = "C:\Users\cfish\win-projects\snapotter",
  [string]$Base = "http://127.0.0.1:1349",
  [switch]$RotateKey,
  [switch]$KeepTestingKey
)
$ErrorActionPreference = "Stop"
$Src = $PSScriptRoot
$BinDir = Join-Path $env:USERPROFILE ".local\bin"
$Dest = Join-Path $BinDir "snapotter"
$KeyDir = Join-Path $env:USERPROFILE ".config\snapotter"
$KeyFile = Join-Path $KeyDir "key"

Write-Host "== 1. CLI"
$python = (Get-Command python -ErrorAction Stop).Source
New-Item -ItemType Directory -Force $Dest | Out-Null
Copy-Item (Join-Path $Src "snapotter.py"), (Join-Path $Src "tools.json") $Dest -Force
Set-Content -Encoding ascii (Join-Path $BinDir "snapotter.cmd") "@`"$python`" `"%USERPROFILE%\.local\bin\snapotter\snapotter.py`" %*"
$onPath = ($env:Path -split ";") | Where-Object { $_.TrimEnd("\") -ieq $BinDir }
if (-not $onPath) { Write-Host "  note: $BinDir is not on PATH in this shell" }
Write-Host "  installed to $Dest (python: $python)"

function Read-EnvValue([string]$name) {
  $line = Get-Content (Join-Path $StackDir ".env") | Where-Object { $_ -like "$name=*" } | Select-Object -First 1
  if (-not $line) { throw "$name not found in $StackDir\.env" }
  $line.Substring($name.Length + 1)
}
function Get-AdminToken {
  $body = @{ username = "craig"; password = (Read-EnvValue "SNAPOTTER_ADMIN_PASSWORD") } | ConvertTo-Json
  (Invoke-RestMethod -Method Post -Uri "$Base/api/auth/login" -ContentType "application/json" -Body $body).token
}

Write-Host "== 2. Scoped API key"
if ($RotateKey -or -not (Test-Path $KeyFile)) {
  $token = Get-AdminToken
  $headers = @{ Authorization = "Bearer $token" }
  $expires = (Get-Date).ToUniversalTime().AddDays(365).ToString("yyyy-MM-ddTHH:mm:ssZ")
  $req = @{ name = "snapotter-cli"; permissions = @("tools:use"); expiresAt = $expires } | ConvertTo-Json
  $created = Invoke-RestMethod -Method Post -Uri "$Base/api/v1/api-keys" -Headers $headers -ContentType "application/json" -Body $req
  New-Item -ItemType Directory -Force $KeyDir | Out-Null
  if (Test-Path $KeyFile) { Remove-Item $KeyFile -Force }
  New-Item -ItemType File -Path $KeyFile | Out-Null
  # Owner-only before the secret is written: no inherited ACEs, you alone.
  cmd /c "icacls `"$KeyFile`" /inheritance:r /grant:r `"$($env:USERNAME):(R,W)`" >nul"
  if ($LASTEXITCODE -ne 0) { throw "could not restrict $KeyFile" }
  [IO.File]::WriteAllText($KeyFile, $created.key)
  Write-Host "  created key '$($created.name)' ($($created.permissions -join ', ')), expires $($created.expiresAt)"
  $created = $null
} else {
  Write-Host "  keeping the existing key in $KeyFile (use -RotateKey to replace it)"
}

Write-Host "== 3. Prove the key"
$cli = Join-Path $BinDir "snapotter.cmd"
cmd /c "`"$cli`" status"
if ($LASTEXITCODE -ne 0) { throw "snapotter status failed" }
$probeDir = Join-Path $env:TEMP "snapotter-install-check"
New-Item -ItemType Directory -Force $probeDir | Out-Null
$probe = Join-Path $probeDir "probe.png"
Copy-Item (Join-Path $Src "..\..\tests\fixtures\image\valid\test-200x150.png") $probe -Force
cmd /c "`"$cli`" run convert `"$probe`" --set format=webp --force --quiet"
if ($LASTEXITCODE -ne 0) { throw "a tool run with the new key failed" }
$keyHeaders = @{ Authorization = "Bearer $([IO.File]::ReadAllText($KeyFile).Trim())" }
try {
  Invoke-RestMethod -Uri "$Base/api/v1/api-keys" -Headers $keyHeaders | Out-Null
  throw "the CLI key can list API keys; it must not"
} catch [System.Net.WebException] {
  $status = [int]$_.Exception.Response.StatusCode
  if ($status -ne 403 -and $status -ne 401) { throw "unexpected HTTP $status from the admin probe" }
  Write-Host "  tool run OK; admin call refused (HTTP $status)"
}
$keyHeaders = $null
Remove-Item $probeDir -Recurse -Force

if (-not $KeepTestingKey) {
  Write-Host "== 4. Revoke the testing key"
  $token = Get-AdminToken
  $headers = @{ Authorization = "Bearer $token" }
  $keys = (Invoke-RestMethod -Uri "$Base/api/v1/api-keys" -Headers $headers).apiKeys
  foreach ($k in $keys | Where-Object { $_.name -eq "claude-local" }) {
    Invoke-RestMethod -Method Delete -Uri "$Base/api/v1/api-keys/$($k.id)" -Headers $headers | Out-Null
    Write-Host "  revoked '$($k.name)' ($($k.keyPrefix))"
  }
  $old = Join-Path $StackDir "api-key.txt"
  if (Test-Path $old) { Remove-Item $old -Force; Write-Host "  removed $old" }
  $remaining = (Invoke-RestMethod -Uri "$Base/api/v1/api-keys" -Headers $headers).apiKeys |
    ForEach-Object { "$($_.name) [$((@($_.permissions)) -join ',')]" }
  Write-Host "  keys now: $($remaining -join '; ')"
}
Write-Host "SNAPOTTER CLI INSTALLED"

<#
.SYNOPSIS
  Promote a verified candidate build to the running release, keeping the
  outgoing release for rollback.
.DESCRIPTION
  snapotter-local:cu134      the running release (compose.raider.yaml default)
  snapotter-local:cu134-next a candidate built by Build-Image.ps1 -Image ...
  snapotter-local:cu134-prev the release before the last promotion

  Promote:  .\scripts\blackwell\Promote-Image.ps1
            cu134 -> cu134-prev, cu134-next -> cu134, then restarts the stack.
  Rollback: .\scripts\blackwell\Promote-Image.ps1 -Rollback
            cu134-prev -> cu134, then restarts the stack.
  Only tags move; no image is deleted. Remove cu134-prev yourself once the new
  release has proved itself (docker rmi snapotter-local:cu134-prev).
#>
param(
  [switch]$Rollback,
  [string]$StackDir = "C:\Users\cfish\win-projects\snapotter"
)
$ErrorActionPreference = "Stop"
function Id([string]$tag) { (cmd /c "docker image inspect -f {{.Id}} $tag 2>nul").Trim() }

$release = "snapotter-local:cu134"; $next = "snapotter-local:cu134-next"; $prev = "snapotter-local:cu134-prev"
if ($Rollback) {
  if (-not (Id $prev)) { throw "no $prev to roll back to" }
  cmd /c "docker tag $prev $release"
  Write-Host "Rolled back: $release is now $((Id $release).Substring(7, 12))"
} else {
  if (-not (Id $next)) { throw "no $next; build one with Build-Image.ps1 -Image $next" }
  if ((Id $next) -eq (Id $release)) { throw "$next is already the release" }
  if (Id $release) { cmd /c "docker tag $release $prev" }
  cmd /c "docker tag $next $release"
  cmd /c "docker rmi $next >nul"
  Write-Host "Promoted: $release is now $((Id $release).Substring(7, 12)); previous kept as $prev"
}

$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Copy-Item (Join-Path $Repo "scripts\blackwell\compose.raider.yaml") (Join-Path $StackDir "compose.yaml") -Force
Remove-Item Env:SNAPOTTER_IMAGE -ErrorAction SilentlyContinue
Push-Location $StackDir
try { cmd /c "docker compose up -d --force-recreate snapotter 2>&1" } finally { Pop-Location }
for ($i = 0; $i -lt 60; $i++) {
  $h = (cmd /c "docker inspect -f {{.State.Health.Status}} snapotter 2>nul").Trim()
  if ($h -eq "healthy") { break }
  Start-Sleep 5
}
if ($h -ne "healthy") { throw "snapotter did not become healthy (last: $h)" }
cmd /c "docker logs snapotter 2>&1" | Select-String -Pattern "Hardware encoders enabled" | Select-Object -First 1 | ForEach-Object { $_.Line }
Write-Host "STACK HEALTHY on $release"

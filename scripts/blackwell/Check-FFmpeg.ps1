<#
.SYNOPSIS
  Phase 2 check of the NVENC FFmpeg build (PRD criteria 1 and 5).
.DESCRIPTION
  1. Inventories ffmpeg in the previous image (-Old) and the new one (-New)
     and lists everything the new build lost.
  2. Runs the app's own one-frame trial encode for h264/hevc/av1_nvenc and an
     NVDEC decode in the new image with the GPU and the `video` capability.
  Output: <repo>\..\snapotter-build\phase3\ffmpeg\ (inventories, diff.txt).
  Exits non-zero when a decoder or demuxer was lost (other than the two known
  unpackaged ones, AVS3 and EVC) or a probe fails.
#>
param(
  [string]$Old = "snapotter-local:cu134",
  [string]$New = "snapotter-local:cu134-nvenc"
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Out = Join-Path (Split-Path $Repo -Parent) "snapotter-build\phase3\ffmpeg"
New-Item -ItemType Directory -Force $Out | Out-Null
$Inv = Join-Path $PSScriptRoot "ffmpeg-inventory.sh"

foreach ($pair in @(@("old", $Old), @("new", $New))) {
  cmd /c "docker run --rm --entrypoint sh -v `"${Inv}:/inv.sh:ro`" $($pair[1]) /inv.sh > `"$Out\$($pair[0]).txt`" 2>&1"
  if ($LASTEXITCODE -ne 0) { throw "inventory of $($pair[1]) failed" }
}
$old = Get-Content "$Out\old.txt"; $new = Get-Content "$Out\new.txt"
$lost = $old | Where-Object { $_ -notin $new }
$gained = $new | Where-Object { $_ -notin $old }
$known = @("decoders libuavs3d", "decoders libxevd", "decoders evc")
# Input protocols are reported but not enforced: the app passes
# -protocol_whitelist file,pipe on every input (security fix S7), so network
# protocols the static build had (rtmp, srt, ssh, zmq, ...) are never reachable.
$lostInput = $lost | Where-Object { $_ -match '^(decoders|demuxers) ' -and $_ -notin $known }
@(
  "== lost (all kinds): $($lost.Count)"; $lost
  ""; "== lost decoders/demuxers, excluding known AVS3/EVC: $($lostInput.Count)"; $lostInput
  ""; "== gained: $($gained.Count)"; ($gained -join " ")
) | Set-Content -Encoding ascii "$Out\diff.txt"
Get-Content "$Out\diff.txt" | Select-Object -First 60

Write-Host "== NVENC / NVDEC probes in $New"
$probe = @'
set -e
for e in h264_nvenc hevc_nvenc av1_nvenc; do
  ffmpeg -hide_banner -loglevel error -f lavfi -i nullsrc=s=256x256 -frames:v 1 -c:v $e -f null - && echo "$e OK"
done
ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=1920x1080:rate=30 -t 2 -c:v libx264 -pix_fmt yuv420p -y /tmp/s.mp4
ffmpeg -hide_banner -loglevel error -hwaccel cuda -i /tmp/s.mp4 -f null - && echo "nvdec OK"
ffmpeg -hide_banner -version | head -1
'@
$probeFile = Join-Path $Out "probe.sh"
[IO.File]::WriteAllText($probeFile, $probe.Replace("`r`n", "`n"))
cmd /c "docker run --rm --gpus all -e NVIDIA_DRIVER_CAPABILITIES=compute,utility,video --entrypoint sh -v `"${probeFile}:/probe.sh:ro`" $New /probe.sh > `"$Out\probe.log`" 2>&1"
$probeExit = $LASTEXITCODE
Get-Content "$Out\probe.log"
if ($probeExit -ne 0) { throw "NVENC/NVDEC probe failed; see $Out\probe.log" }
if ($lostInput.Count -gt 0) { throw "the new build lost $($lostInput.Count) input capabilities; see $Out\diff.txt" }
Write-Host "FFMPEG CHECK OK"

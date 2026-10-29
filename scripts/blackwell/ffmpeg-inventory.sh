#!/bin/sh
# Print the ffmpeg build's capabilities as "<kind> <name>" lines, sorted.
# Used by Check-FFmpeg.ps1 to diff the fork's NVENC build against the
# previous image's static build. Usage: ffmpeg-inventory.sh [ffmpeg-binary]
set -eu
FF=${1:-ffmpeg}
rows() { "$FF" -hide_banner -"$1" 2>/dev/null | awk -v k="$1" 'f&&NF{print k, $2} /^ *-+ *$/{f=1}'; }
fmts() { "$FF" -hide_banner -"$1" 2>/dev/null | awk -v k="$1" 'f&&NF{print k, $2} /^ *--/{f=1}'; }
{
  rows decoders
  rows encoders
  rows filters
  fmts demuxers
  fmts muxers
  "$FF" -hide_banner -protocols 2>/dev/null | awk '/^Input:/{i=1;next} /^Output:/{i=0} i&&NF{print "protocols-in", $1}'
} | sort -u

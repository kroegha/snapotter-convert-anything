#!/bin/sh
# Phase 2 test clips (PRD section 9). Runs in the NVENC image with the GPU.
# There is no representative sample clip, so the clips are built from the
# repo's real footage: stress-large.jpg is a 4000x3000 Galaxy S24 Ultra photo
# (real sensor detail and noise), panned like a handheld shot; media-30s.mp4 is
# Big Buck Bunny (real motion), upscaled with temporal grain added. Sources are
# encoded at the bitrates phones and screen recorders use (HEVC 1080p ~16 Mb/s,
# HEVC 4K30 ~40 Mb/s, H.264 1440p60 ~12 Mb/s), so the long clips stay under
# the stack's 4 GB upload limit.
#   phone.mp4    1080x1920 portrait, 30 fps, HEVC 10-bit, 60 s
#   long4k.mp4   3840x2160, 30 fps, HEVC 10-bit, 10 min (40 s base, looped)
#   screen.mp4   2560x1440, 60 fps, H.264, 10 min (60 s base, looped)
# Usage: make_clips.sh <fixtures-dir> <repo-dir> <out-dir>
set -eu
FX=$1; REPO=$2; OUT=$3
mkdir -p "$OUT"; cd "$OUT"
PHOTO="$FX/image/valid/stress-large.jpg"
BBB="$FX/video/valid/media-30s.mp4"
FONT=/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf
hevc10() { echo "-c:v hevc_nvenc -profile:v main10 -pix_fmt p010le -rc vbr -b:v $1 -maxrate $2 -bufsize $2 -preset p7 -tag:v hvc1"; }
FF="ffmpeg -hide_banner -loglevel error -y"

# Base segment: <seconds> of photo pan, then <seconds> of film, with grain.
base() { # w h secs out bitrate maxrate
  w=$1; h=$2; s=$3; out=$4; enc=$(hevc10 "$5" "$6")
  $FF -loop 1 -framerate 30 -t "$s" -i "$PHOTO" -t "$s" -i "$BBB" -stream_loop -1 -t $((s*2)) -i "$BBB" \
    -filter_complex "\
[0:v]scale=-2:$((h*16/10)):flags=lanczos,crop=$w:$h:x='(iw-ow)*t/$s':y='(ih-oh)/2+(ih-oh)/4*sin(t*0.9)',setsar=1,fps=30,format=yuv420p[a];\
[1:v]scale=$w:$h:force_original_aspect_ratio=increase:flags=lanczos,crop=$w:$h,setsar=1,fps=30,format=yuv420p[b];\
[a][b]concat=n=2:v=1:a=0,noise=alls=7:allf=t,format=p010le[v]" \
    -map "[v]" -map 2:a $enc -c:a aac -b:a 160k -movflags +faststart "$out"
}

echo "phone.mp4"
base 1080 1920 30 phone.mp4 16M 24M

echo "long4k.mp4"
base 3840 2160 20 long4k-base.mp4 40M 60M
$FF -stream_loop 14 -i long4k-base.mp4 -c copy -movflags +faststart long4k.mp4
rm -f long4k-base.mp4

echo "screen.mp4"
# A code editor scrolling through real source, with a moving cursor block.
cp "$REPO/packages/media-engine/src/encoders.ts" /tmp/screen.txt
$FF -f lavfi -i "color=c=0x1e1e1e:s=2560x1440:r=60:d=60" -f lavfi -i "anullsrc=r=48000:cl=stereo" -t 60 \
  -vf "drawbox=x=0:y=0:w=320:h=1440:color=0x252526:t=fill,\
drawtext=fontfile=$FONT:textfile=/tmp/screen.txt:expansion=none:fontsize=24:line_spacing=8:fontcolor=0xd4d4d4:x=360:y='60-t*55',\
drawtext=fontfile=$FONT:text='_':fontsize=30:fontcolor=0xffffff:x='360+mod(t*240,1800)':y=700,format=yuv420p" \
  -c:v h264_nvenc -rc vbr -b:v 12M -maxrate 18M -bufsize 18M -preset p7 -c:a aac -b:a 96k -movflags +faststart screen-base.mp4
$FF -stream_loop 9 -i screen-base.mp4 -c copy -movflags +faststart screen.mp4
rm -f screen-base.mp4 /tmp/screen.txt

for f in phone.mp4 long4k.mp4 screen.mp4; do
  ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,profile,width,height,r_frame_rate,pix_fmt \
    -show_entries format=duration,size -of compact=p=0:nk=0 "$f" | tr '\n' ' '; echo " <- $f"
done
echo "CLIPS DONE"

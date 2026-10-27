#!/usr/bin/env bash
# phonecam.sh — read a phone screen through the Logitech StreamCam.
#
# Captures a short burst, scores every frame with ffmpeg's blurdetect, keeps the
# sharpest one. Emissive screens need CONTINUOUS AUTOFOCUS (a locked manual focus
# is the usual cause of unreadable captures) and moderate gain, otherwise the
# panel's white UI clips and the glyphs merge.
#
# usage: scripts/phonecam.sh [outdir] [cw|ccw|none]
#   outdir   default /tmp/opencode/phonecam
#   rotate   rotate the kept frame (phone usually lies sideways to the camera)
#
# env: PHONECAM_DEV (default /dev/video0), PHONECAM_GAIN (48), PHONECAM_BRIGHTNESS (100)
#
# After the fact, crop the region of interest and upscale with e.g.
#   ffmpeg -i best.jpg -vf "crop=W:H:X:Y,scale=iw*4:ih*4:flags=lanczos,unsharp=5:5:0.8" out.jpg
set -euo pipefail

OUT=${1:-/tmp/opencode/phonecam}
ROT=${2:-none}
DEV=${PHONECAM_DEV:-/dev/video0}
GAIN=${PHONECAM_GAIN:-48}
BRIGHT=${PHONECAM_BRIGHTNESS:-100}

command -v v4l2-ctl >/dev/null || { echo "v4l2-ctl missing: apt install v4l-utils" >&2; exit 1; }
command -v ffmpeg  >/dev/null || { echo "ffmpeg missing: apt install ffmpeg" >&2; exit 1; }
[ -e "$DEV" ] || { echo "no camera at $DEV" >&2; exit 1; }

mkdir -p "$OUT"

# Emissive screen: autofocus on, do not let AGC blow out the white UI.
v4l2-ctl -d "$DEV" --set-ctrl=focus_automatic_continuous=1 >/dev/null 2>&1 || true
v4l2-ctl -d "$DEV" --set-ctrl=gain="$GAIN"                        >/dev/null 2>&1 || true
v4l2-ctl -d "$DEV" --set-ctrl=brightness="$BRIGHT"                >/dev/null 2>&1 || true
v4l2-ctl -d "$DEV" --set-ctrl=zoom_absolute=100                   >/dev/null 2>&1 || true

rm -f "$OUT"/f*.jpg
ffmpeg -hide_banner -loglevel error -y -f v4l2 -input_format mjpeg \
       -video_size 1920x1080 -i "$DEV" -t 2 -vsync 0 "$OUT"/f%03d.jpg >/dev/null 2>&1 || true

best=""; bestb=""
for f in "$OUT"/f*.jpg; do
    [ -f "$f" ] || continue
    b=$(ffmpeg -hide_banner -loglevel info -i "$f" \
          -vf "blurdetect,metadata=print:file=-" -f null - 2>/dev/null \
        | grep -o 'lavfi.blur=[0-9.]*' | head -1 | cut -d= -f2)
    [ -n "$b" ] || continue
    if [ -z "$bestb" ] || awk "BEGIN{exit !($b < $bestb)}"; then
        bestb=$b; best=$f
    fi
done

[ -n "$best" ] || { echo "no frames captured" >&2; exit 1; }

case "$ROT" in
    cw)  VF="transpose=1" ;;
    ccw) VF="transpose=2" ;;
    *)   VF="null" ;;
esac
ffmpeg -hide_banner -loglevel error -y -i "$best" -vf "$VF" -q:v 2 "$OUT/best.jpg"

echo "$OUT/best.jpg"
echo "blur=$bestb source=$best frames=$(ls "$OUT"/f*.jpg | wc -l)"

#!/usr/bin/env bash
# Contact sheets (10 frames, 1 per 3 s) for mined clips not yet approved or rejected.
# -> state/review/<id>.jpg plus state/review/<id>.txt (title, caption, judge reason). Prints the ids.
cd "$(dirname "$0")/.." || exit 1
mkdir -p state/review
touch state/replay_approved.txt state/replay_rejected.txt
[ -f state/replay_accepted.jsonl ] || exit 0
for id in $(python3 -c "import json;[print(json.loads(l)['id']) for l in open('state/replay_accepted.jsonl') if l.strip()]"); do
  grep -qx "$id" state/replay_approved.txt state/replay_rejected.txt && continue
  [ -f "clips/$id.mp4" ] || continue
  [ -f "state/review/$id.jpg" ] || ffmpeg -v error -y -i "clips/$id.mp4" -vf "fps=1/3,scale=270:-2,tile=5x2" -frames:v 1 "state/review/$id.jpg"
  python3 -c "import json;m=json.load(open('clips/$id.json'));print('title:',m['title']);print('caption:',m['caption']);print('reason:',m['reason'])" > "state/review/$id.txt"
  echo "$id"
done

"""Cinematic scene plates (Starting Soon / BRB / Ending) of the streamer, via Codex image generation on the ChatGPT
plan, likeness-checked against the face sheet. Saves state/overlay/scene_<name>.png.

    .venv/bin/python -m kit.scene_art [starting brb ending]
    .venv/bin/python -m kit.scene_art --poses [starting_front ...]   one try each -> state/overlay/poses/<key>.png
    .venv/bin/python -m kit.scene_art --fix a.png b.png              redo only the face from the face sheet
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config as c, stream_meta as sm

FACE = c.ROOT / "state" / "face" / "face_ref_lr.jpg"  # real photos with glasses, both sides: best likeness
OUTFIT = c.ROOT / "state" / "face" / "style_ref_outfit.png"  # his costume turnaround with the head cropped out
OUT = c.STATE / "overlay"

LOOK = ("cinematic GTA VI loading-screen key art: painterly 3D with crisp detail, Vice City dusk palette of deep violet "
        "#2D0A64, hot pink #FF37A0 and sunset orange #FF9628, anamorphic lens, motivated neon and sun light, soft haze, "
        "subtle film grain, refined colour grade, premium game-cinematic finish. " + sm.STYLE_HERO_TEXT)
# Exactly his costume (state/face/style_ref_outfit.png). Earlier runs drifted: long gauntlets, different jacket.
WEAR = ("He wears EXACTLY the costume in the attached OUTFIT image (second image; it has no head, take only the "
        "clothes from it): a red jacket with faceted polygon-pattern angular shoulder plates, high collar and small "
        "silver buckle tabs on the chest straps; a dark charcoal fitted top with a chevron pattern and a small round "
        "camera-aperture emblem on the chest; a belt with a rectangular silver buckle with a red inset; dark grey "
        "trousers with angular knee armour and red thigh accents; chunky dark boots with small cyan lights. Forearms: "
        "SHORT dark-grey armoured bracers (wrist to mid-forearm only) with a tiny cyan screen, and short black "
        "fingerless gloves with small cyan knuckle studs. Normal human hands and forearm length; no long gauntlets, "
        "no extra plates. Hair: long dark hair slicked back flat and close to the head, tied in a small bun at the back, "
        "as in the photos (not puffy, not voluminous).")
# Body: earlier runs gave a big head on a thin body. His build is the one in the OUTFIT image.
BODY = ("Realistic adult male proportions: head height about one-seventh of his full body height, head NOT oversized; "
        "broad strong shoulders about three head-widths across and a solid athletic build exactly like the body in the "
        "OUTFIT image (not skinny, not slim-waisted, not stretched); arms and legs in natural proportion to the torso.")
COMPOSE = ("Wide 16:9 frame. His WHOLE head, hair and bun are fully inside the frame with generous empty space above "
           "his head: the top 22% of the image is sky or background only, nothing of him there. Medium shot from the "
           "knees or waist up, camera at chest height. {pose} Calm natural confident expression with mouth closed. "
           "Keep the LEFT 45% of the frame calm and uncluttered (sky, soft "
           "background) for a title. No text, letters, logos, signs with words, watermarks or UI anywhere.")
SCENES = {
    "starting": "golden-pink dusk on a palm-lined ocean-front boulevard; he leans back against the hood of a pastel "
                "1980s muscle car, glancing at his wristwatch as if the night is about to begin; neon strips on the "
                "art-deco hotels flicker on, a police cruiser's lights glow far down the road",
    "brb": "late night at a rain-wet neon gas station; he walks out of the 24/7 store holding a cold soda and a paper "
           "snack bag, his car parked at the pump behind him, pink and teal reflections in the puddles",
    "ending": "blue hour on a long causeway bridge; he sits on the roof of his parked car watching the glittering "
              "skyline across the water as a jet streaks overhead, warm tail lights trailing past",
}
SIDE = "He stands in the RIGHT third, turned three-quarters, not posing at the camera."
FRONT = ("He is in the RIGHT third (centre of him at about 70% of the width), body and face turned FRONT-ON to the "
         "camera, looking straight into the lens.")
THREEQ = "He is in the RIGHT third, body turned three-quarters toward the left of frame, face in three-quarter view."
PROFILE = ("He is in the RIGHT third in clean SIDE PROFILE facing left, his face and glasses clearly visible in profile.")
LOOKBACK = ("He is in the RIGHT third, body turned away from the camera, looking back over his shoulder straight into "
            "the lens so his face is clearly visible.")
LOW = ("Low camera angle looking slightly up at him (heroic), he is in the RIGHT third in three-quarter view, face clearly "
       "visible.")
# Extra poses to choose from: key -> (scene, action replacing the scene's action, framing)
POSES = {
    "starting_front": ("starting", "he walks straight toward the camera down the palm-lined boulevard at golden-pink dusk, "
                       "pulling his fingerless glove tight, his pastel 1980s muscle car parked behind him with headlights on", FRONT),
    "starting_hood": ("starting", "he sits on the hood of his pastel 1980s muscle car on the ocean-front boulevard at dusk, "
                      "forearms resting on his knees, neon hotels lighting up behind him", FRONT),
    "brb_front": ("brb", "he stands under the neon canopy of a rain-wet 24/7 gas station at night sipping a cold soda, "
                  "paper snack bag in the other hand, his car at the pump behind him", FRONT),
    "brb_phone": ("brb", "he leans against his car at the pump of a neon gas station at night, glancing up from his "
                  "phone, pink and teal reflections in the puddles", FRONT),
    "ending_front": ("ending", "he stands at the railing of a long causeway bridge at blue hour, the glittering skyline "
                     "behind him, raising two fingers in a small relaxed goodbye salute", FRONT),
    "ending_drive": ("ending", "he sits behind the wheel of his convertible on a causeway at blue hour, seen through the "
                     "open front, one hand on the wheel, the skyline and tail lights behind", FRONT),
    "starting_lookback": ("starting", "he walks toward his pastel 1980s muscle car on the dusk boulevard, keys in hand, "
                          "glancing back over his shoulder", LOOKBACK),
    "starting_low": ("starting", "he stands with one hand on the open door of his pastel muscle car, looking down the "
                     "neon-lit boulevard as the night begins", LOW),
    "brb_profile": ("brb", "he stands at the lit counter window of the neon 24/7 store paying for snacks", PROFILE),
    "brb_keys": ("brb", "he walks back to his car at the pump twirling a small car key fob on a ring around one finger, soda in the other hand",
                 THREEQ),
    "ending_profile": ("ending", "he sits on the railing of the long causeway bridge watching the glittering skyline, a "
                       "jet streaking overhead", PROFILE),
    "ending_door": ("ending", "he opens the door of his car on the causeway at blue hour and turns his head to the "
                    "camera with a small nod goodbye", LOOKBACK),
}
PROMPT = ("Use your built-in image generation tool (do NOT draw with code, PIL, SVG or any script) to create ONE image "
          "and save it as plate.png in the current directory.\nSubject (first image = his face photos): {subject} {wear} {body}\nStyle: {look}\nScene: "
          "{scene}.\n{compose}\nForbidden: official GTA or Marvel characters, costumes or logos, beautified or "
          "altered face, oversized head, skinny or slim body, wide grin, smirk, extra fingers, cluttered composition, {forbid}.")


FIX = ("Use your built-in image generation tool (no code, no PIL, no scripts) to EDIT the FIRST attached image and save "
       "the result as plate.png in the current directory. Keep EVERYTHING identical (pose, body, outfit, car, scene, "
       "lighting, framing, art style) and change ONLY his face and hair so he is {subject} The SECOND image holds his "
       "real face photos: match his face shape, eyes, nose, lips, beard, skin tone and glasses exactly, with long dark "
       "hair slicked back flat into a small bun. Keep the head EXACTLY the same size and position as in the first image; "
       "do not enlarge the head or change the body. Calm natural expression, mouth closed.")


def gen(prompt, images):
    """One Codex image run -> path of the PNG (plate.png, or the newest generated image), or None."""
    d = tempfile.mkdtemp()
    t0 = time.time()
    try:
        sm.run_codex(prompt, d, "workspace-write", 900, image=images)
    except Exception as e:
        print("codex failed:", e, flush=True)
        return None
    p = Path(d) / "plate.png"
    if p.is_file():
        return p
    imgs = [q for q in (Path.home() / ".codex" / "generated_images").rglob("*.png") if q.stat().st_mtime > t0]
    return max(imgs, key=lambda q: q.stat().st_mtime) if imgs else None


def face_fix(path):
    """Second pass: redo only face and hair from the face sheet (the outfit image pulls the face away).
    Measured on the hood pose: likeness 0.586 -> 0.398. Returns (mean, path) of the fixed copy or None."""
    p = gen(FIX.format(subject=sm.SUBJECT_REF), [path, FACE])
    if not p:
        return None
    out = path.with_name(path.stem + "_fix.png")
    shutil.copy(p, out)
    sc = (sm.likeness([out]) or {}).get(str(out))
    print(path.stem, "face-fix likeness", sc, flush=True)
    return (sc["mean"] if sc else 9.0, out)


ANATOMY = ("You check AI character art before a streamer sees it. Read img.png (the candidate) and ref.png (his real "
           "costume and body build, no head). Fail it for ANY of: head too large for the body (head height clearly more "
           "than about 1/6 of body height, or head wider than about 1/2 of shoulder width), body thinner or narrower "
           "than the build in ref.png, stretched or short limbs, wrong hands (extra or missing fingers, warped), long "
           "gauntlets instead of short bracers, costume clearly different from ref.png, hair puffy or cut off at the "
           "frame edge, objects in his hands that are not what the scene says (e.g. keys that look like a tool), "
           "readable text or brand logos in the scene. The round chest emblem is part of his costume and is allowed. "
           "Judge visible parts only; a cropped or seated pose is fine if what is visible is in proportion. Reply only with the JSON the schema asks for.")
ANATOMY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["pass", "issues"],
                  "properties": {"pass": {"type": "boolean"}, "issues": {"type": "string"}}}


def anatomy(path):
    """Claude (Sonnet, Max plan via `claude -p`) proportions/costume gate. Returns (ok, issues); ok=None on error."""
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(path, Path(d) / "img.png")
        shutil.copy(OUTFIT, Path(d) / "ref.png")
        cmd = ["claude", "-p", ANATOMY, "--model", "sonnet", "--output-format", "json",
               "--json-schema", json.dumps(ANATOMY_SCHEMA), "--allowedTools", "Read", "--safe-mode", "--tools", "Read",
               "--system-prompt", "Use Read on the two image files, then reply only with the JSON the schema asks for.",
               "--disallowedTools", "Bash,Edit,Write,WebFetch,WebSearch", "--strict-mcp-config",
               "--disable-slash-commands", "--no-session-persistence"]
        try:
            r = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=300)
            env = json.loads(r.stdout)
            v = env.get("structured_output") or json.loads(env.get("result") or "{}")
            print(path.name, "anatomy", v, flush=True)
            return bool(v.get("pass")), v.get("issues", "")
        except Exception as e:
            print(path.name, "anatomy check failed:", e, flush=True)
            return None, str(e)


def one(name, tries=3, out=None, pose=SIDE, scene=None):
    out = out or OUT
    best = None
    for attempt in range(tries):
        with tempfile.TemporaryDirectory() as d:
            t0 = time.time()
            prompt = PROMPT.format(subject=sm.SUBJECT_REF, wear=WEAR, body=BODY, look=LOOK, scene=scene or SCENES[name], compose=COMPOSE.format(pose=pose),
                                   forbid=sm.FORBID_HERO)
            try:
                sm.run_codex(prompt, d, "workspace-write", 900, image=[FACE, OUTFIT])
            except Exception as e:
                print(name, "codex failed:", e, flush=True)
                continue
            p = Path(d) / "plate.png"
            if not p.is_file():  # fall back to the newest image Codex generated during this run
                imgs = [q for q in (Path.home() / ".codex" / "generated_images").rglob("*.png") if q.stat().st_mtime > t0]
                p = max(imgs, key=lambda q: q.stat().st_mtime) if imgs else None
            if not p:
                continue
            keep = out / f"{name}_try{attempt + 1}.png" if out != OUT else OUT / f"scene_{name}_try{attempt + 1}.png"
            shutil.copy(p, keep)
            score = (sm.likeness([keep]) or {}).get(str(keep))
            mean = score["mean"] if score else 9.0
            print(name, "try", attempt + 1, "likeness", score, flush=True)
            if mean > 0.40:  # likeness too weak: try the face-fix pass on this plate
                fixed = face_fix(keep)
                if fixed and fixed[0] < mean:
                    mean, keep = fixed
            ok, _ = anatomy(keep)
            if ok is False:  # wrong proportions or costume: never keep it, whatever the face score
                continue
            if best is None or mean < best[0]:
                best = (mean, keep)
            if mean <= 0.40:
                break
    if best:
        shutil.copy(best[1], out / f"{name}.png" if out != OUT else OUT / f"scene_{name}.png")
    return name, best and best[0]


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    args = sys.argv[1:]
    if args[:1] == ["--fix"]:  # face-fix existing images: --fix a.png b.png
        with ThreadPoolExecutor(len(args) - 1) as ex:
            print(json.dumps({Path(a).stem: r and r[0] for a, r in zip(args[1:], ex.map(lambda a: face_fix(Path(a)), args[1:]))}))
        sys.exit()
    if args[:1] == ["--poses"]:
        keys, dst = args[1:] or list(POSES), OUT / "poses"
        dst.mkdir(exist_ok=True)
        run = lambda k: one(k, int(c.s("SCENE_TRIES") or 2), dst, POSES[k][2], SCENES[POSES[k][0]].split(";")[0] + "; " + POSES[k][1])
    else:
        keys, run = args or list(SCENES), one
    with ThreadPoolExecutor(len(keys)) as ex:
        print(json.dumps(dict(ex.map(run, keys))))

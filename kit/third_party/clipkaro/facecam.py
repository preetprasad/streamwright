# Adapted from https://github.com/allthingsaman/ClipKaro @ b6d9401ac83fc695c8ce52795c14c25213a866fd
# (clipper/facecam.py + the YuNet detector from clipper/reframe.py), MIT, see LICENSE next to this file.
# Changes: round-cam (Hough circle) branch dropped, paths/constants adapted, mask support removed.
"""Gameplay + webcam overlay: find the cam, and build the stacked 1080x1920 ffmpeg filter."""
from pathlib import Path

import cv2
import numpy as np

W, H = 1080, 1920
TOP_H = 1150                 # gameplay panel height; the cam panel gets the rest
CAP_GAP = 10                 # caption line sits just below the seam
GAME_TRIM = 0.14             # drop the bottom 14% of the game frame: the game's own subtitles live there
CAM_TOP_PAD, CAM_BOTTOM_PAD = 90, 30   # room for the captions above the cam
YUNET = Path(__file__).resolve().parents[3] / "models" / "face_detection_yunet_2023mar.onnx"
_DET = {}


def detector(w, h):
    if "d" not in _DET:
        _DET["d"] = cv2.FaceDetectorYN.create(str(YUNET), "", (w, h), 0.6, 0.3, 5000)
    _DET["d"].setInputSize((w, h))
    return _DET["d"]


def faces(frame):
    """[(cx, cy, face_height)] in source pixels (detection runs on a 640 px wide copy)."""
    scale = 640 / frame.shape[1]
    small = cv2.resize(frame, (640, int(frame.shape[0] * scale)))
    _, found = detector(*small.shape[1::-1]).detect(small)
    if found is None:
        return []
    return [((x + w / 2) / scale, (y + h / 2) / scale, h / scale) for x, y, w, h in found[:, :4] if w >= 18]


def sample(video, duration, n=24):
    cap = cv2.VideoCapture(str(video))
    out = []
    for k in range(n):
        cap.set(cv2.CAP_PROP_POS_MSEC, duration * (k + 0.5) / n * 1000)
        ok, frame = cap.read()
        if ok:
            out.append(frame)
    cap.release()
    return out


def detect(video, duration, src_w, src_h):
    """{'x','y','w','h'} (source px) of a webcam overlay, or None."""
    frames = sample(video, duration)
    if len(frames) < 6:
        return None
    per_frame = [faces(f) for f in frames]
    tol = 0.03 * src_w
    best, best_hits = None, []
    for fs in per_frame:
        for c in fs:
            hits = [near[0] for others in per_frame
                    if (near := [f for f in others if abs(f[0] - c[0]) < tol and abs(f[1] - c[1]) < tol])]
            if len(hits) > len(best_hits):
                best, best_hits = c, hits
    if not best or len(best_hits) < max(4, 0.5 * len(frames)):
        return None
    cx, cy, fh = (float(v) for v in np.median(np.array(best_hits), axis=0))
    # a cam overlay is small and sits towards a side; a presenter's face in a normal video isn't both
    if fh > 0.2 * src_h or 0.28 * src_w < cx < 0.72 * src_w:
        return None
    bh = min(src_h, 3.0 * fh)
    bw = min(src_w, bh * 4 / 3)
    return {"x": cx - bw / 2, "y": cy - bh * 0.45, "w": bw, "h": bh}


def clamp(cam, src_w, src_h):
    x, y = max(0, int(cam["x"])), max(0, int(cam["y"]))
    return {"x": x, "y": y, "w": min(src_w - x, int(cam["w"])) // 2 * 2, "h": min(src_h - y, int(cam["h"])) // 2 * 2}


def geometry(cam, src_w, src_h):
    """Where everything goes in the 1080x1920 frame."""
    bot_h = H - TOP_H
    gh = int(src_h * (1 - GAME_TRIM)) // 2 * 2                 # gameplay crop height, subtitle strip cut off
    gw = min(src_w, int(gh * W / TOP_H) // 2 * 2)
    gx = (src_w - gw) // 2
    if cam["x"] < gx + gw and cam["x"] + cam["w"] > gx:       # don't show the cam twice
        right = cam["x"] + cam["w"] / 2 > src_w / 2
        gx = max(0, min(int(cam["x"]) - gw, gx)) if right else min(src_w - gw, max(int(cam["x"] + cam["w"]), gx))
    room_w, room_h = W - 2 * 60, bot_h - CAM_TOP_PAD - CAM_BOTTOM_PAD
    s = min(room_w / cam["w"], room_h / cam["h"])
    cw, ch = int(cam["w"] * s) // 2 * 2, int(cam["h"] * s) // 2 * 2
    return {"gx": gx // 2 * 2, "gw": gw, "gh": gh, "bot_h": bot_h, "cam_w": cw, "cam_h": ch, "cam_x": (W - cw) // 2,
            "cam_y": TOP_H + CAM_TOP_PAD + (room_h - ch) // 2, "caption_y": TOP_H + CAP_GAP}


def filter_chain(cam, g, src_h, fps):
    """ffmpeg filter: input [0:v] -> stacked 1080x1920 [v0]. Gameplay on top, blurred+darkened copy with the cam below."""
    return ";".join([
        "[0:v]split=3[g][b][c]",
        f"[g]crop={g['gw']}:{g['gh']}:{g['gx']}:0,scale={W}:{TOP_H}:flags=lanczos,setsar=1[top]",
        f"[b]scale={W}:{g['bot_h']}:force_original_aspect_ratio=increase,crop={W}:{g['bot_h']},"
        "boxblur=30:3,eq=brightness=-0.12,setsar=1[bg]",
        f"[c]crop={cam['w']}:{cam['h']}:{cam['x']}:{cam['y']},scale={g['cam_w']}:{g['cam_h']}:flags=lanczos,setsar=1[cam]",
        f"[bg][cam]overlay={g['cam_x']}:{g['cam_y'] - TOP_H}:shortest=1[bot]",
        f"[top][bot]vstack=inputs=2,setsar=1,fps={fps},format=yuv420p[v0]"])

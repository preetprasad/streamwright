"""Face likeness scorer, run with a Python that has `face_recognition` (dlib), e.g. the system python3.

    python3 kit/likeness_score.py REF_IMAGE [REF_IMAGE ...] -- CANDIDATE [CANDIDATE ...]

Prints JSON: {"refs": n, "scores": {candidate: {"mean": d, "best": d} | null}}. Distances are dlib face
distances: lower = more alike, < 0.6 = same person. Every face found in each reference image counts (a FACE_REF
sheet holds several views). For a candidate, the largest face is scored. Kept separate from the kit's venv because
dlib is heavy to build there.
"""
import json
import sys

import face_recognition as fr
import numpy as np


def encodings(path, largest_only=False):
    im = fr.load_image_file(path)
    locs = fr.face_locations(im, number_of_times_to_upsample=1)
    if largest_only and locs:
        locs = [max(locs, key=lambda b: (b[2] - b[0]) * (b[1] - b[3]))]
    return fr.face_encodings(im, locs)


def main(argv):
    if "--" not in argv:
        sys.exit(__doc__)
    i = argv.index("--")
    refs = [e for p in argv[:i] for e in encodings(p)]
    out = {"refs": len(refs), "scores": {}}
    for c in argv[i + 1:]:
        e = encodings(c, largest_only=True)
        if not refs or not e:
            out["scores"][c] = None
            continue
        d = np.linalg.norm(np.array(refs) - e[0], axis=1)
        out["scores"][c] = {"mean": round(float(d.mean()), 3), "best": round(float(d.min()), 3)}
    print(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv[1:])

"""Offline check of the StabilizedCrop window solve and round-trip math.

Stubs out `nuke` and `nuke.rotopaint` so the pure-geometry functions can be
exercised without Nuke. Run with:

    python3 tests/test_solve.py
"""
import math
import os
import sys
import types

nuke_stub = types.ModuleType("nuke")
for name in ("Text_Knob", "Int_Knob", "Double_Knob", "XY_Knob", "PyScript_Knob",
             "Enumeration_Knob", "ProgressTask"):
    setattr(nuke_stub, name, object)
nuke_stub.INVISIBLE = 1
nuke_stub.STARTLINE = 2
nuke_stub.nodes = types.SimpleNamespace()
nuke_stub.root = lambda: None
nuke_stub.message = lambda *a: None
nuke_stub.menu = lambda *a: None
nuke_stub.createNode = lambda *a, **k: None
nuke_stub.selectedNodes = lambda: []
nuke_stub.thisNode = lambda: None
nuke_stub.thisKnob = lambda: None

rp_stub = types.ModuleType("nuke.rotopaint")
rp_stub.Shape = type("Shape", (), {})
rp_stub.Layer = type("Layer", (), {})
nuke_stub.rotopaint = rp_stub
sys.modules["nuke"] = nuke_stub
sys.modules["nuke.rotopaint"] = rp_stub

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "stabilized_crop", "python"))
import stabilized_crop as sc

PLATE_W, PLATE_H = 1920, 1080
failures = []


def check(label, cond, detail=""):
    if not cond:
        failures.append("{}: {}".format(label, detail))
    print("{:4} {}  {}".format("ok" if cond else "FAIL", label, detail))


def round_trip(boxes, res_w, res_h, plate_w=PLATE_W, plate_h=PLATE_H):
    """Mirror _apply's chain and assert plate -> crop -> plate is identity."""
    windows, clipped = sc._solve_windows(boxes, res_w, res_h, plate_w, plate_h)
    ref_x, ref_y = windows[0][1], windows[0][2]
    for frame, wx, wy in windows:
        stab_x, stab_y = ref_x - wx, ref_y - wy          # Stabilize.translate
        mm_x, mm_y = wx - ref_x, wy - ref_y              # Matchmove.translate
        for u, v in ((0, 0), (res_w // 2, res_h // 3), (res_w, res_h)):
            # forward: plate pixel -> stabilize -> crop(box at ref, reformat)
            px, py = wx + u, wy + v
            sx, sy = px + stab_x, py + stab_y
            cx, cy = sx - ref_x, sy - ref_y              # crop w/ reformat
            assert (cx, cy) == (u, v), (frame, u, v, cx, cy)
            # reverse: crop pixel -> ResultPlace(+ref) -> Matchmove
            bx, by = cx + ref_x + mm_x, cy + ref_y + mm_y
            if (bx, by) != (px, py):
                return False, windows, clipped, (frame, (px, py), (bx, by))
    return True, windows, clipped, None


# --- 1. moving element, window well inside the plate ------------------------
boxes = [(f, 500 + f * 3, 400 + f * 2, 700 + f * 3, 660 + f * 2) for f in range(1, 51)]
okay, windows, clipped, bad = round_trip(boxes, 1024, 1024)
check("round trip identity (interior)", okay, bad or "50 frames x 3 probes")
check("no clipping when res > bbox", clipped == [], "clipped={}".format(clipped))
# element must be centred in the crop within a pixel
for (f, x, y, r, t), (_f, wx, wy) in zip(boxes, windows):
    off = abs(((x + r) // 2 - wx) - 512)
    assert off <= 1, (f, off)
check("element centred in crop", True, "max offset <= 1px")

# --- 2. element hugging the left/bottom edge -> window slides inward -------
boxes = [(f, 10, 5, 210, 265) for f in range(1, 11)]
okay, windows, clipped, bad = round_trip(boxes, 1024, 1024)
check("round trip identity (clamped at origin)", okay, bad or "")
check("window clamped to plate origin", all(w[1] == 0 and w[2] == 0 for w in windows),
      str(windows[0]))

# --- 3. element hugging the top/right edge --------------------------------
boxes = [(f, 1800, 1000, 1919, 1079) for f in range(1, 11)]
okay, windows, clipped, bad = round_trip(boxes, 1024, 1024)
check("round trip identity (clamped at max)", okay, bad or "")
check("window clamped to plate max",
      all(w[1] == PLATE_W - 1024 and w[2] == PLATE_H - 1024 for w in windows),
      str(windows[0]))
check("window never leaves plate",
      all(0 <= w[1] <= PLATE_W - 1024 and 0 <= w[2] <= PLATE_H - 1024 for w in windows))

# --- 4. bbox larger than requested res -> clip must be reported ------------
boxes = [(f, 400, 300, 1600, 900) for f in range(1, 6)]      # 1200 x 600 bbox
okay, windows, clipped, bad = round_trip(boxes, 1024, 1024)
check("round trip identity (oversize bbox)", okay, bad or "")
check("oversize bbox reported as clipped", len(clipped) == 5,
      "clipped {} of 5".format(len(clipped)))
mw, mh = sc._bbox_extremes(boxes)
check("_bbox_extremes", (mw, mh) == (1200, 600), "{} x {}".format(mw, mh))
fit_w = int(math.ceil(mw / 8.0) * 8)
check("fit_res rounds up to /8", fit_w == 1200 and fit_w % 8 == 0, str(fit_w))

# --- 5. res taller than the plate -> centred, symmetric overscan -----------
boxes = [(f, 800, 400, 1000, 700) for f in range(1, 4)]
okay, windows, clipped, bad = round_trip(boxes, 1024, 1536, PLATE_W, PLATE_H)
check("round trip identity (res > plate height)", okay, bad or "")
check("oversize res centred on plate", windows[0][2] == (PLATE_H - 1536) // 2,
      "win_y={} expected {}".format(windows[0][2], (PLATE_H - 1536) // 2))

# --- 6. only the reference frame's window becomes the crop box -------------
boxes = [(1, 500, 400, 700, 660), (2, 900, 400, 1100, 660)]
_okay, windows, _c, _b = round_trip(boxes, 512, 512)
ref = windows[0]
check("crop box is static across frames", True,
      "box=({}, {}, {}, {}) for all frames".format(ref[1], ref[2], ref[1] + 512, ref[2] + 512))
check("stabilize translate is integer",
      all(isinstance(ref[1] - w[1], int) for w in windows))

print()
if failures:
    print("{} FAILURE(S):".format(len(failures)))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all checks passed")

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


def round_trip(boxes, res_w, res_h, plate_w=PLATE_W, plate_h=PLATE_H, offsets=None):
    """Mirror _apply's chain and assert plate -> crop -> plate is identity."""
    windows, clipped, _held = sc._solve_windows(
        boxes, res_w, res_h, plate_w, plate_h, offsets)
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
fit_w, fit_h = sc._round_up(mw), sc._round_up(mh)
check("fit_res rounds up to a multiple of RES_STEP",
      fit_w % sc.RES_STEP == 0 and fit_h % sc.RES_STEP == 0,
      "{} x {} (step {})".format(fit_w, fit_h, sc.RES_STEP))
check("fit_res still contains the bbox", fit_w >= mw and fit_h >= mh,
      "{}x{} contains {}x{}".format(fit_w, fit_h, mw, mh))
check("fit_res does not overshoot by a whole step",
      fit_w - mw < sc.RES_STEP and fit_h - mh < sc.RES_STEP,
      "slack {} x {}".format(fit_w - mw, fit_h - mh))

# --- 4b. every preset must satisfy the model's stride requirement -----------
bad = []
for preset in sc.RES_PRESETS:
    if preset == "custom":
        continue
    pw, ph = (int(v) for v in preset.split(" x "))
    if pw % sc.RES_STEP or ph % sc.RES_STEP:
        bad.append(preset)
check("all presets divisible by RES_STEP", bad == [], "offenders: {}".format(bad))

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

# --- 7. plate size cache survives a disconnect -----------------------------
# Regression: _apply runs on inputChange, so unplugging the plate used to fall
# back to the project format and silently re-bake against the wrong numbers.
PROJECT_W, PROJECT_H = 4096, 2160          # deliberately not the plate format


class FakeFormat(object):
    def __init__(self, width, height):
        self._w, self._h = width, height

    def width(self):
        return self._w

    def height(self):
        return self._h


class FakePlate(object):
    def __init__(self, width, height):
        self._fmt = FakeFormat(width, height)

    def format(self):
        return self._fmt


class FakeKnob(object):
    def __init__(self, values=(0.0, 0.0)):
        self._values = list(values)

    def value(self, index):
        return self._values[index]

    def setValue(self, value, index):
        self._values[index] = value


class FakeNode(object):
    """Just enough node for _plate_size, _read_plate_cache, _store_plate_size."""

    def __init__(self, cache=None, plate=None):
        self._knobs = {}
        if cache is not None:
            self._knobs["plate_size"] = FakeKnob((float(cache[0]), float(cache[1])))
        self._plate = plate

    def knob(self, name):
        return self._knobs.get(name)

    def __getitem__(self, name):
        return self._knobs[name]

    def input(self, index):
        return self._plate if index == 0 else None


nuke_stub.root = lambda: types.SimpleNamespace(
    format=lambda: FakeFormat(PROJECT_W, PROJECT_H))

# a node predating the cache knob keeps the old live-input behaviour
node = FakeNode(cache=None, plate=FakePlate(PLATE_W, PLATE_H))
check("no cache falls back to the live input", sc._plate_size(node) == (PLATE_W, PLATE_H, ""),
      str(sc._plate_size(node)))

# the case that used to corrupt the bake
node = FakeNode(cache=(PLATE_W, PLATE_H), plate=None)
check("cached size survives plate disconnect",
      sc._plate_size(node) == (PLATE_W, PLATE_H, ""),
      "got {} (project format is {}x{})".format(sc._plate_size(node), PROJECT_W, PROJECT_H))

node = FakeNode(cache=(PLATE_W, PLATE_H), plate=FakePlate(PLATE_W, PLATE_H))
check("matching plate produces no warning", sc._plate_size(node) == (PLATE_W, PLATE_H, ""),
      str(sc._plate_size(node)))

node = FakeNode(cache=(PLATE_W, PLATE_H), plate=FakePlate(PROJECT_W, PROJECT_H))
width, height, warning = sc._plate_size(node)
check("swapped plate warns but does not move the bake",
      (width, height) == (PLATE_W, PLATE_H) and warning.startswith("!"), warning)

node = FakeNode(cache=(1, 1), plate=None)
sc._store_plate_size(node, PLATE_W, PLATE_H)
check("_store_plate_size round trips", sc._read_plate_cache(node) == (PLATE_W, PLATE_H),
      str(sc._read_plate_cache(node)))

# why the cache matters: plate size changes the solve whenever clamping engages
edge = [(f, 1700, 900, 1900, 1060) for f in range(1, 4)]
on_plate, _c, _h = sc._solve_windows(edge, 1024, 1024, PLATE_W, PLATE_H)
on_project, _c, _h = sc._solve_windows(edge, 1024, 1024, PROJECT_W, PROJECT_H)
check("wrong plate size really would move the window", on_plate != on_project,
      "{} vs {}".format(on_plate[0], on_project[0]))

# --- 8. crop offset ---------------------------------------------------------
# well inside the plate, so nothing clamps and the offset lands in full
boxes = [(f, 800 + f, 400 + f, 1000 + f, 700 + f) for f in range(1, 11)]
base, _c, _h = sc._solve_windows(boxes, 512, 512, PLATE_W, PLATE_H)
shift = {f: (40, -25) for f in range(1, 11)}
moved, _c, held = sc._solve_windows(boxes, 512, 512, PLATE_W, PLATE_H, shift)
check("offset shifts the window by exactly the requested amount",
      all((m[1] - b[1], m[2] - b[2]) == (40, -25) for b, m in zip(base, moved)),
      "{} -> {}".format(base[0], moved[0]))
check("unclamped offset is not reported as held", held == [], str(held))

okay, _w, _c, bad = round_trip(boxes, 512, 512, offsets=shift)
check("round trip identity survives an offset", okay, bad or "10 frames x 3 probes")

# animated offset: a different shift on every frame, still pixel exact
ramp = {f: (f * 7, -f * 3) for f in range(1, 11)}
okay, windows, _c, bad = round_trip(boxes, 512, 512, offsets=ramp)
check("round trip identity survives an animated offset", okay, bad or "")
check("animated offset differs per frame", windows[0][1] != windows[-1][1] - 9,
      "{} .. {}".format(windows[0], windows[-1]))

# pushing into a plate edge: window holds, and it is reported
edge = [(f, 60, 500, 260, 700) for f in range(1, 4)]
held_windows, _c, held = sc._solve_windows(edge, 512, 512, PLATE_W, PLATE_H,
                                           {f: (-400, 0) for f in range(1, 4)})
check("offset into the plate edge is clamped", all(w[1] == 0 for w in held_windows),
      str(held_windows[0]))
check("clamped offset is reported", len(held) == 3, "held on {} of 3".format(len(held)))

# a zero offset must be indistinguishable from no offset at all
same, _c, held = sc._solve_windows(boxes, 512, 512, PLATE_W, PLATE_H,
                                   {f: (0, 0) for f in range(1, 11)})
check("zero offset changes nothing", same == base and held == [], str(same[0]))

# --- 9. version discipline --------------------------------------------------
# The git tag has to match __version__, so keep it parseable.
parts = sc.__version__.split(".")
check("__version__ is a semver triple",
      len(parts) == 3 and all(p.isdigit() for p in parts), sc.__version__)
check("version label is what gets stamped on a node",
      sc._version_label() == "StabilizedCrop v" + sc.__version__, sc._version_label())

print()
if failures:
    print("{} FAILURE(S):".format(len(failures)))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all checks passed")

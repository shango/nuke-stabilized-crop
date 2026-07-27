"""
StabilizedCrop - fixed-resolution stabilized crop for AI inpainting round trips.
================================================================================

Builds a single Group node that:

  input 0  plate      the footage
  input 1  roto       Roto / RotoPaint driving the bounding box
  input 2  result     the sequence that comes back from ComfyUI

  mode = crop         output is a fixed WxH stabilized crop of the plate,
                      with the roto matte carried in alpha
  mode = comp         output is the result placed back into plate space and
                      comped over the plate through the matte

  "use plate alpha" swaps the matte from the roto to the plate's own alpha, on
  both branches at once, so after Analyze the roto can be unplugged entirely.

The bbox is sampled once from the roto control points (no rendering) and cached
on hidden animated knobs, so changing the crop resolution re-solves instantly.

Every transform in both directions is an integer pixel translation with an
impulse filter, so the crop -> comp round trip is pixel exact.

Usage
-----
    import stabilized_crop
    stabilized_crop.create()          # or register_menu() once in menu.py

Then plug in plate + roto, press "Analyze roto", set the resolution, render the
output with mode = crop. Tick "alpha only" and render again for the black and
white mask; it is the same crop, so the two line up exactly.

Self-contained - this is the only file you need to deploy. Targets Nuke 15.x /
16.x.
"""

import math

import nuke
import nuke.rotopaint as rp

# Bump on release, and tag the repo to match.
#   patch  behaviour fixes inside existing functions. Reach nodes already saved
#          in scripts, because the buttons import this module at click time.
#   minor  anything touching _build_internals or _add_knobs. Nodes already saved
#          keep their old internals and need rebuilding to pick it up.
#   major  renaming a public function or this file. Breaks saved nodes.
__version__ = "1.5.0"

MENU_LABEL = "Stabilized Crop (fixed res)"
SUBMENU_LABEL = "Convert"

# Common latent-friendly buckets. "custom" leaves res_w / res_h alone.
RES_PRESETS = [
    "custom",
    "512 x 512",
    "768 x 768",
    "1024 x 1024",
    "1152 x 896",
    "896 x 1152",
    "1216 x 832",
    "832 x 1216",
    "1344 x 768",
    "768 x 1344",
    "1536 x 1536",
    "2048 x 2048",
]

MODES = ["crop", "comp"]

# "Set res to fit bbox" rounds up to a multiple of this. Latent diffusion models
# work on an 8x downsampled latent and most UNets downsample a further 8x, so
# dimensions off a multiple of 32 get silently padded or rejected. Every entry in
# RES_PRESETS above is already a multiple of 64.
RES_STEP = 32


# ---------------------------------------------------------------------------
# World-space roto bbox math, inlined verbatim from roto_to_bbox.py so this
# module ships as a single file. If you fix the bbox math there, port it here.
# ---------------------------------------------------------------------------

def bvfx_roto_walker(rotoNode, rotoList=None):
    """Traverse the roto node hierarchy tree and generate a list with [element, parent].

    Ignores Strokes. Handles both a Roto/RotoPaint Node (top-level) and
    a Layer (recursive).

    Returns:
        list of (Shape|Layer, parentLayer)
    """
    if rotoList is None:
        rotoList = []

    try:
        if rotoNode.Class() in ("Roto", "RotoPaint"):
            rotoRoot = rotoNode["curves"].rootLayer
            rotoList = []
    except Exception:
        rotoRoot = rotoNode

    for layer in rotoRoot:
        if isinstance(layer, rp.Shape):
            rotoList.append((layer, rotoRoot))
        if isinstance(layer, rp.Layer):
            rotoList.append((layer, rotoRoot))
            bvfx_roto_walker(layer, rotoList)
    return rotoList


def bvfx_TTM(point, transf, frame):
    """Apply a Transform's extra matrix to a point (Transform To Matrix).

    Args:
        point: (x, y) tuple
        transf: Transform with evaluate(frame).getMatrix() method
        frame: frame to evaluate
    Returns:
        (x, y, z) tuple in transformed space
    """
    matrix = transf.evaluate(frame).getMatrix()
    vector = nuke.math.Vector4(point[0], point[1], 1, 1)
    x = (vector[0] * matrix[0]) + (vector[1] * matrix[1]) + matrix[2] + matrix[3]
    y = (vector[0] * matrix[4]) + (vector[1] * matrix[5]) + matrix[6] + matrix[7]
    z = (vector[0] * matrix[8]) + (vector[1] * matrix[9]) + matrix[10] + matrix[11]
    w = (vector[0] * matrix[12]) + (vector[1] * matrix[13]) + matrix[14] + matrix[15]
    vector = nuke.math.Vector4(x, y, z, w)
    vector = vector / w
    return vector


def bvfx_TL(point, Layer, frame, shapeList):
    """Recursively apply Layers' matrix/transforms on a point up to roto.root.

    Args:
        point: (x, y) tuple
        Layer: the layer to apply the transform from
        frame: frame to evaluate
        shapeList: result of bvfx_roto_walker()
    Returns:
        (x, y, z) tuple
    """
    newPoint = bvfx_TTM(point, Layer.getTransform(), frame)

    # shapeList[0][1] always holds roto.root, so we stop at the root
    if not Layer == shapeList[0][1]:
        for s in shapeList:
            if s[0] is Layer:
                newPoint = bvfx_TL(newPoint, s[1], frame, shapeList)
                break
    return newPoint


def compute_baked_bbox_at_frame(shapeList, frame):
    """Compute the bounding box of all shapes in the walker list at a given frame.

    Transforms from extra matrix (getTransform) and parent layers (via bvfx_TL)
    are applied to every control point position before computing bounds.

    Args:
        shapeList: result of bvfx_roto_walker()
        frame: frame to evaluate

    Returns:
        (min_x, min_y, max_x, max_y) or None if no visible shapes
    """
    min_x = float("inf")
    min_y = float("inf")
    max_x = float("-inf")
    max_y = float("-inf")
    found = False

    for shape, parentLayer in shapeList:
        if not isinstance(shape, rp.Shape):
            continue

        # Get the shape's local transform (includes extra matrix from Roto/Transform)
        try:
            transf = shape.getTransform()
        except Exception:
            transf = None

        for cp in shape:
            # Get the raw position of this CV
            curve_x = cp.center.getPositionAnimCurve(0)
            curve_y = cp.center.getPositionAnimCurve(1)
            raw_x = curve_x.evaluate(frame)
            raw_y = curve_y.evaluate(frame)

            # Apply shape's local transform (extra matrix)
            if transf is not None:
                baked_point = bvfx_TTM((raw_x, raw_y), transf, frame)
            else:
                baked_point = (raw_x, raw_y, 0)

            # Apply parent layer transforms recursively
            if parentLayer is not None:
                final = bvfx_TL((baked_point[0], baked_point[1]), parentLayer, frame, shapeList)
            else:
                final = baked_point

            px, py = final[0], final[1]
            if px < min_x:
                min_x = px
            if py < min_y:
                min_y = py
            if px > max_x:
                max_x = px
            if py > max_y:
                max_y = py
            found = True

    if not found:
        return None

    return (min_x, min_y, max_x, max_y)


# ---------------------------------------------------------------------------
# bbox sampling
# ---------------------------------------------------------------------------

def _sample_bbox(roto_node, first, last):
    """Sample the roto bbox for every frame in the range.

    Returns [(frame, x, y, r, t), ...] using the same control-point + matrix
    math as roto_to_bbox, so no rendering is needed.
    """
    shape_list = bvfx_roto_walker(roto_node)
    if not shape_list:
        raise ValueError("no shapes found inside '{}'".format(roto_node.name()))

    samples = []
    total = max(1, last - first + 1)
    task = nuke.ProgressTask("Sampling roto bbox")
    try:
        for i, frame in enumerate(range(first, last + 1)):
            if task.isCancelled():
                raise RuntimeError("cancelled")
            task.setProgress(int(100.0 * i / total))
            task.setMessage("Frame {}".format(frame))

            box = compute_baked_bbox_at_frame(shape_list, frame)
            if box is None:
                continue
            samples.append((frame, box[0], box[1], box[2], box[3]))
    finally:
        del task

    if not samples:
        raise ValueError("roto produced no bbox over frames {}-{}".format(first, last))
    return samples


def _store_bbox(node, samples):
    """Bake sampled bboxes onto the node's hidden animated knobs."""
    for knob_name in ("bbox_lo", "bbox_hi"):
        knob = node[knob_name]
        knob.clearAnimated()
        knob.setAnimated()

    lo = node["bbox_lo"]
    hi = node["bbox_hi"]
    for frame, x, y, r, t in samples:
        # floor the low corner and ceil the high corner so the integer box
        # always fully contains the shape
        lo.setValueAt(float(math.floor(x)), frame, 0)
        lo.setValueAt(float(math.floor(y)), frame, 1)
        hi.setValueAt(float(math.ceil(r)), frame, 0)
        hi.setValueAt(float(math.ceil(t)), frame, 1)


def _read_bbox(node):
    """Read the cached bboxes back off the hidden knobs.

    Returns [(frame, x, y, r, t), ...] or [] if the node has not been analyzed.
    """
    lo = node["bbox_lo"]
    hi = node["bbox_hi"]
    if not lo.isAnimated(0):
        return []

    frames = sorted(int(round(key.x)) for key in lo.animation(0).keys())
    boxes = []
    for frame in frames:
        boxes.append((
            frame,
            int(round(lo.valueAt(frame, 0))),
            int(round(lo.valueAt(frame, 1))),
            int(round(hi.valueAt(frame, 0))),
            int(round(hi.valueAt(frame, 1))),
        ))
    return boxes


# ---------------------------------------------------------------------------
# window solve
# ---------------------------------------------------------------------------

def _child(group, name):
    """Return the group's internal node called *name*."""
    for node in group.nodes():
        if node.name() == name:
            return node
    raise ValueError("'{}' has no internal node named '{}'".format(group.name(), name))


def _live_plate_size(node):
    """Format size of the plate input right now, or the project format."""
    plate = node.input(0)
    if plate is not None:
        try:
            fmt = plate.format()
            if fmt is not None and fmt.width() > 0 and fmt.height() > 0:
                return int(fmt.width()), int(fmt.height())
        except Exception:
            pass
    fmt = nuke.root().format()
    return int(fmt.width()), int(fmt.height())


def _store_plate_size(node, width, height):
    """Record the plate format the analysis was solved against."""
    knob = node["plate_size"]
    knob.setValue(float(width), 0)
    knob.setValue(float(height), 1)


def _read_plate_cache(node):
    """Plate size recorded at Analyze time, or None if there isn't one.

    Returns None for nodes built before this knob existed so they keep working
    on the old live-input behaviour rather than erroring.
    """
    knob = node.knob("plate_size")
    if knob is None:
        return None
    width, height = int(knob.value(0)), int(knob.value(1))
    if width < 1 or height < 1:
        return None
    return width, height


def _plate_size(node):
    """Plate size for the solve. Returns (width, height, warning).

    Prefers the size recorded at Analyze time over the live input. Plate size
    only enters the math through clamping, but that is enough: without the
    cache, disconnecting the plate falls back to the project format and
    silently re-bakes the transforms against the wrong numbers. The cache never
    changes on its own, so the bake can only move when Analyze is pressed.
    """
    cached = _read_plate_cache(node)
    if cached is None:
        width, height = _live_plate_size(node)
        return width, height, ""

    warning = ""
    if node.input(0) is not None and _live_plate_size(node) != cached:
        live_w, live_h = _live_plate_size(node)
        warning = ("! plate is {} x {} but was analyzed at {} x {}"
                   " - press Analyze roto".format(live_w, live_h, *cached))
    return cached[0], cached[1], warning


def _read_offsets(node, frames):
    """Per-frame crop offset as {frame: (dx, dy)}, or None if there is none.

    Read with valueAt so animating the offset works. Any per-frame integer
    shift stays pixel exact, because the window position it produces drives
    both directions of the round trip.

    Returns None for nodes built before these knobs existed, and also when the
    offset is zero everywhere, so an unused offset changes nothing.
    """
    x_knob, y_knob = node.knob("offset_x"), node.knob("offset_y")
    if x_knob is None or y_knob is None:
        return None

    offsets = {}
    for frame in frames:
        offsets[frame] = (int(round(x_knob.valueAt(frame))),
                          int(round(y_knob.valueAt(frame))))
    if not any(dx or dy for dx, dy in offsets.values()):
        return None
    return offsets


def _solve_windows(boxes, res_w, res_h, plate_w, plate_h, offsets=None):
    """Place a fixed res_w x res_h window on the bbox centre of every frame.

    The window is clamped to stay inside the plate, so near the frame edges the
    element drifts within the crop rather than pulling in off-plate pixels. If
    the window is wider or taller than the plate it is centred instead, which is
    the only case that can introduce black.

    offsets shifts the window, as {frame: (dx, dy)}. It is applied before the
    clamp, so the crop still cannot leave the plate; frames where the plate edge
    absorbed some of the shift come back in held_frames. Integer only, which is
    what keeps the round trip pixel exact.

    Returns (windows, clipped_frames, held_frames) where
        windows        - [(frame, win_x, win_y), ...] integer lower-left corners
        clipped_frames - frames whose bbox does not fit inside its window
        held_frames    - frames where the plate edge limited a nonzero offset
    """
    def _place(lo, hi, size, plate_size, offset):
        """Returns (origin, was_clamped)."""
        centre = int(round((lo + hi) * 0.5))
        origin = centre - size // 2 + offset
        if size <= plate_size:
            limited = max(0, min(plate_size - size, origin))
            return limited, limited != origin
        # window bigger than the plate: centre it and accept the overscan
        return (plate_size - size) // 2 + offset, False

    windows = []
    clipped = []
    held = []
    for frame, x, y, r, t in boxes:
        off_x, off_y = offsets.get(frame, (0, 0)) if offsets else (0, 0)
        win_x, held_x = _place(x, r, res_w, plate_w, off_x)
        win_y, held_y = _place(y, t, res_h, plate_h, off_y)
        windows.append((frame, win_x, win_y))
        if x < win_x or y < win_y or r > win_x + res_w or t > win_y + res_h:
            clipped.append(frame)
        if (off_x and held_x) or (off_y and held_y):
            held.append(frame)
    return windows, clipped, held


def _bbox_extremes(boxes):
    """Return (max_w, max_h) of the largest single-frame bbox."""
    max_w = max(r - x for _, x, _y, r, _t in boxes)
    max_h = max(t - y for _, _x, y, _r, t in boxes)
    return max_w, max_h


# ---------------------------------------------------------------------------
# writing the solve into the group
# ---------------------------------------------------------------------------

def _bake_translate(transform, values):
    """Set Transform.translate from [(frame, tx, ty), ...] as integers."""
    knob = transform["translate"]
    knob.clearAnimated()
    knob.setAnimated()
    for frame, tx, ty in values:
        knob.setValueAt(float(int(tx)), frame, 0)
        knob.setValueAt(float(int(ty)), frame, 1)


def _apply(node):
    """Re-solve the crop window from the cached bbox and push it into the group.

    Cheap - no roto sampling - so this can run on every knob change.
    """
    boxes = _read_bbox(node)
    if not boxes:
        node["report_bbox"].setValue("bbox: press Analyze roto")
        node["report_clip"].setValue("")
        return

    res_w = int(node["res_w"].value())
    res_h = int(node["res_h"].value())
    if res_w < 1 or res_h < 1:
        node["report_clip"].setValue("! resolution must be at least 1 x 1")
        return

    plate_w, plate_h, plate_warning = _plate_size(node)
    offsets = _read_offsets(node, [frame for frame, _x, _y, _r, _t in boxes])
    windows, clipped, held = _solve_windows(
        boxes, res_w, res_h, plate_w, plate_h, offsets)

    # Frame 1 of the solve defines the static crop box; every other frame is
    # translated so its window lands on that same box.
    ref_x, ref_y = windows[0][1], windows[0][2]

    stabilize = [(frame, ref_x - win_x, ref_y - win_y) for frame, win_x, win_y in windows]
    matchmove = [(frame, win_x - ref_x, win_y - ref_y) for frame, win_x, win_y in windows]

    _bake_translate(_child(node, "Stabilize"), stabilize)
    _bake_translate(_child(node, "Matchmove"), matchmove)

    crop = _child(node, "CropWindow")
    crop_box = crop["box"]
    crop_box.clearAnimated()
    for index, value in enumerate((ref_x, ref_y, ref_x + res_w, ref_y + res_h)):
        crop_box.setValue(float(value), index)

    place = _child(node, "ResultPlace")
    place["translate"].clearAnimated()
    place["translate"].setValue(float(ref_x), 0)
    place["translate"].setValue(float(ref_y), 1)

    # report
    max_w, max_h = _bbox_extremes(boxes)
    node["report_bbox"].setValue(
        "max bbox: {} x {} px     travel: {} x {} px".format(
            max_w, max_h,
            max(r for _f, _x, _y, r, _t in boxes) - min(x for _f, x, _y, _r, _t in boxes),
            max(t for _f, _x, _y, _r, t in boxes) - min(y for _f, _x, y, _r, _t in boxes),
        )
    )

    warnings = []
    if plate_warning:
        warnings.append(plate_warning)
    if clipped:
        warnings.append("! bbox clipped on {} of {} frames (first {})".format(
            len(clipped), len(boxes), clipped[0]))
    if held:
        warnings.append("! offset limited by plate edge on {} of {} frames".format(
            len(held), len(boxes)))
    if res_w > plate_w or res_h > plate_h:
        warnings.append("! res exceeds plate {} x {} - edges will be black".format(
            plate_w, plate_h))
    node["report_clip"].setValue("     ".join(warnings))


# ---------------------------------------------------------------------------
# button / callback entry points
# ---------------------------------------------------------------------------

def analyze(node):
    """Sample the roto bbox over the range, cache it, then re-solve."""
    roto = node.input(1)
    if roto is None:
        nuke.message("Connect a Roto or RotoPaint to the 'roto' input.")
        return
    # The window is clamped to the plate, so the solve is only meaningful with
    # a plate to measure. Refusing here also keeps a project-format guess from
    # being cached as though it were the real thing.
    if node.input(0) is None:
        nuke.message("Connect the plate to the 'plate' input before analyzing.")
        return
    if roto.Class() not in ("Roto", "RotoPaint"):
        nuke.message("'{}' is a {} - the roto input needs a Roto or RotoPaint.".format(
            roto.name(), roto.Class()))
        return

    first = int(node["first"].value())
    last = int(node["last"].value())
    if last < first:
        nuke.message("Last frame is before first frame.")
        return

    try:
        samples = _sample_bbox(roto, first, last)
    except RuntimeError:
        return
    except ValueError as error:
        nuke.message(str(error))
        return

    _store_bbox(node, samples)
    _store_plate_size(node, *_live_plate_size(node))
    _apply(node)


def _version_label():
    """Text stamped onto a node when it is built."""
    return "StabilizedCrop v{}".format(__version__)


def _round_up(size, step=None):
    """Smallest multiple of step that is >= size."""
    step = RES_STEP if step is None else step
    return int(math.ceil(size / float(step)) * step)


def fit_res(node):
    """Round the resolution up to contain the largest bbox, in steps of RES_STEP."""
    boxes = _read_bbox(node)
    if not boxes:
        nuke.message("Press 'Analyze roto' first.")
        return
    max_w, max_h = _bbox_extremes(boxes)
    node["res_w"].setValue(_round_up(max_w))
    node["res_h"].setValue(_round_up(max_h))
    node["res_preset"].setValue("custom")
    _apply(node)


def on_knob_changed():
    """knobChanged handler installed on the group."""
    node = nuke.thisNode()
    knob = nuke.thisKnob()
    name = knob.name()

    if name == "res_preset":
        value = knob.value()
        if value != "custom":
            width, height = value.split(" x ")
            node["res_w"].setValue(int(width))
            node["res_h"].setValue(int(height))
        _apply(node)
    elif name in ("res_w", "res_h"):
        node["res_preset"].setValue("custom")
        _apply(node)
    elif name in ("inputChange", "first", "last", "offset_x", "offset_y"):
        _apply(node)


# ---------------------------------------------------------------------------
# node construction
# ---------------------------------------------------------------------------

def _add_knobs(group):
    """Build the group's user interface."""
    first_frame = int(nuke.root()["first_frame"].value())
    last_frame = int(nuke.root()["last_frame"].value())

    def add(knob, tooltip=None, same_line=False, invisible=False):
        if tooltip:
            knob.setTooltip(tooltip)
        if same_line:
            knob.clearFlag(nuke.STARTLINE)
        if invisible:
            knob.setFlag(nuke.INVISIBLE)
        group.addKnob(knob)
        return knob

    add(nuke.Text_Knob("div_analyze", "bounding box"))

    add(nuke.Int_Knob("first", "range"),
        "First frame to sample the roto over.")
    add(nuke.Int_Knob("last", ""),
        "Last frame to sample the roto over.", same_line=True)
    group["first"].setValue(first_frame)
    group["last"].setValue(last_frame)

    add(nuke.PyScript_Knob(
        "analyze", "Analyze roto",
        "import stabilized_crop; stabilized_crop.analyze(nuke.thisNode())"),
        "Sample the roto bbox on every frame in the range and cache it on this "
        "node. Run again after editing the roto shapes.")

    add(nuke.Text_Knob("report_bbox", ""))
    add(nuke.Text_Knob("report_clip", ""))

    add(nuke.Text_Knob("div_res", "crop resolution"))

    add(nuke.Enumeration_Knob("res_preset", "preset", RES_PRESETS),
        "Common model resolutions. Choosing one sets the width and height below.")
    add(nuke.Int_Knob("res_w", "resolution"),
        "Crop width in pixels. The plate is not rescaled - this is the size of "
        "the window cut out of it.")
    add(nuke.Int_Knob("res_h", ""),
        "Crop height in pixels.", same_line=True)
    group["res_w"].setValue(1024)
    group["res_h"].setValue(1024)

    add(nuke.PyScript_Knob(
        "fit_res", "Set res to fit bbox",
        "import stabilized_crop; stabilized_crop.fit_res(nuke.thisNode())"),
        "Round the resolution up to the next multiple of {} that contains the "
        "largest bbox, so nothing is clipped.".format(RES_STEP))

    add(nuke.Int_Knob("offset_x", "offset"),
        "Shift the crop window right by this many pixels. Applied before the "
        "clamp, so the crop still cannot leave the plate - the report says when "
        "a plate edge is limiting it. Animatable.")
    add(nuke.Int_Knob("offset_y", ""),
        "Shift the crop window up by this many pixels.", same_line=True)

    add(nuke.Text_Knob("div_out", "output"))

    add(nuke.Enumeration_Knob("mode", "mode", MODES),
        "crop: render this to ComfyUI (matte is in alpha).\n"
        "comp: the 'result' input placed back over the plate.")

    alpha_only = nuke.Boolean_Knob("alpha_only", "alpha only")
    alpha_only.setFlag(nuke.STARTLINE)
    add(alpha_only,
        "Crop mode only. Output the matte as black and white instead of the "
        "picture, so the same Write renders your mask. Same geometry as the "
        "crop, because it is the same crop.")

    plate_alpha = nuke.Boolean_Knob("plate_alpha", "use plate alpha")
    plate_alpha.setFlag(nuke.STARTLINE)
    add(plate_alpha,
        "The matte lives in the plate's alpha rather than the roto. Applies to "
        "both the crop you send out and the comp back, so the roto is only "
        "needed for Analyze. For plates that already arrive with a matte.")

    add(nuke.Int_Knob("matte_grow", "matte grow"),
        "Dilate the roto matte by this many pixels before comping the result "
        "back. Negative shrinks it.")
    add(nuke.Double_Knob("matte_blur", "matte blur"),
        "Soften the comp-back matte edge.")
    group["matte_blur"].setValue(2.0)

    # Stamped once, at build time, and never updated. Names the version that
    # built this node, which is not necessarily the version now installed:
    # behaviour changes reach old nodes, structural ones do not. Called
    # tool_version rather than version because some node classes already have
    # a knob by that name.
    add(nuke.Text_Knob("tool_version", "", _version_label()),
        "Version of stabilized_crop.py that built this node. Compare with "
        "stabilized_crop.__version__ to see what is installed.")

    # cached per-frame bbox, hidden but saved with the script
    add(nuke.XY_Knob("bbox_lo", "bbox lo"), invisible=True)
    add(nuke.XY_Knob("bbox_hi", "bbox hi"), invisible=True)
    add(nuke.XY_Knob("plate_size", "plate size"), invisible=True)


def _build_internals(group):
    """Create the node tree inside the group."""
    group.begin()
    try:
        plate = nuke.nodes.Input(name="plate", number=0, xpos=0, ypos=0)
        roto = nuke.nodes.Input(name="roto", number=1, xpos=150, ypos=0)
        result = nuke.nodes.Input(name="result", number=2, xpos=400, ypos=0)

        # Carry the roto matte in alpha so it travels through the exact same
        # stabilize + crop as the plate. Copy input 0 is B, input 1 is A.
        matte_copy = nuke.nodes.Copy(
            name="AlphaCopy", inputs=[plate, roto], xpos=0, ypos=100)
        matte_copy["from0"].setValue("rgba.alpha")
        matte_copy["to0"].setValue("rgba.alpha")

        # Same knob picks the matte for both branches, so "use plate alpha"
        # means one thing everywhere: the matte lives in the plate, not the
        # roto. 0 takes the roto matte copied in above, 1 leaves the plate's
        # own alpha untouched.
        crop_alpha = nuke.nodes.Switch(
            name="CropAlpha", inputs=[matte_copy, plate], xpos=0, ypos=140)
        crop_alpha["which"].setExpression("parent.plate_alpha")

        stabilize = nuke.nodes.Transform(
            name="Stabilize", inputs=[crop_alpha], xpos=0, ypos=180,
            filter="impulse", label="lock bbox centre")

        crop = nuke.nodes.Crop(
            name="CropWindow", inputs=[stabilize], xpos=0, ypos=260,
            crop=True, reformat=True, label="[value parent.res_w] x [value parent.res_h]")

        # alpha_only = 1 pushes the matte into rgb so the same Write renders a
        # black and white mask. Copy rather than Shuffle because Copy's knob
        # names have been stable across versions and we already rely on them.
        matte_out = nuke.nodes.Copy(
            name="MatteOut", inputs=[crop, crop], xpos=150, ypos=330)
        for index, channel in enumerate(("red", "green", "blue")):
            matte_out["from{}".format(index)].setValue("rgba.alpha")
            matte_out["to{}".format(index)].setValue("rgba.{}".format(channel))

        crop_switch = nuke.nodes.Switch(
            name="CropSwitch", inputs=[crop, matte_out], xpos=0, ypos=400)
        crop_switch["which"].setExpression("parent.alpha_only")

        # --- comp-back branch -------------------------------------------------
        place = nuke.nodes.Transform(
            name="ResultPlace", inputs=[result], xpos=400, ypos=180,
            filter="impulse", label="back into stabilized space")

        matchmove = nuke.nodes.Transform(
            name="Matchmove", inputs=[place], xpos=400, ypos=260,
            filter="impulse", label="back into plate space")

        # Which alpha masks the comp back. 0 = the roto, 1 = the plate's own
        # alpha, for plates that arrive with a matte already in them. Comp mode
        # only; the crop output always carries the roto matte.
        matte_source = nuke.nodes.Switch(
            name="MatteSource", inputs=[roto, plate], xpos=250, ypos=280)
        matte_source["which"].setExpression("parent.plate_alpha")

        grow = nuke.nodes.Dilate(
            name="MatteGrow", inputs=[matte_source], xpos=250, ypos=340,
            channels="alpha")
        grow["size"].setExpression("parent.matte_grow")

        soften = nuke.nodes.Blur(
            name="MatteBlur", inputs=[grow], xpos=250, ypos=400, channels="alpha")
        soften["size"].setExpression("parent.matte_blur")

        # operation "copy" ignores A's alpha, so a flat RGB render from ComfyUI
        # still comps. The mask input restricts it to the roto area.
        comp = nuke.nodes.Merge2(
            name="CompMerge", inputs=[plate, matchmove, soften],
            xpos=0, ypos=470, operation="copy", output="rgb", bbox="B side",
            label="result over plate through matte")
        # maskChannelMask reads the mask INPUT (soften, above). maskChannelInput
        # would read B's own alpha instead, which silently bypasses the whole
        # grow/blur branch - it looks like it works, because B is the plate.
        comp["maskChannelMask"].setValue("rgba.alpha")
        comp["maskChannelInput"].setValue("none")

        switch = nuke.nodes.Switch(
            name="OutSwitch", inputs=[crop_switch, comp], xpos=0, ypos=560)
        switch["which"].setExpression("parent.mode")

        nuke.nodes.Output(inputs=[switch], xpos=0, ypos=640)
    finally:
        group.end()


def create():
    """Create and return a configured StabilizedCrop group."""
    selected = nuke.selectedNodes()
    group = nuke.createNode("Group", inpanel=False)
    group.setName("StabilizedCrop1")
    group["tile_color"].setValue(0x2277BBFF)

    _build_internals(group)
    _add_knobs(group)

    group["knobChanged"].setValue(
        "import stabilized_crop; stabilized_crop.on_knob_changed()")

    # Convenience: wire up a selection. Roto/RotoPaint always goes to the roto
    # input, since nuke.selectedNodes() order is not the selection order.
    rotos = [n for n in selected if n.Class() in ("Roto", "RotoPaint")]
    plates = [n for n in selected if n not in rotos]
    if plates:
        group.setInput(0, plates[0])
    if rotos:
        group.setInput(1, rotos[0])

    _apply(group)
    return group


def register_menu(menu_path=SUBMENU_LABEL, shortcut=""):
    """Add the tool to the Nuke node menu. Call once from a menu.py.

    menu_path may be nested with slashes, so a facility deploy can drop it into
    an existing studio menu:

        stabilized_crop.register_menu("MyStudio/Roto")
    """
    menu = nuke.menu("Nodes").addMenu(menu_path)
    menu.addCommand(
        MENU_LABEL,
        "import stabilized_crop; stabilized_crop.create()",
        shortcut)


if __name__ == "__main__":
    create()

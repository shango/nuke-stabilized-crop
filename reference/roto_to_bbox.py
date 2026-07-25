"""
Roto Bounding Box Tool for Foundry Nuke
==========================================
Creates an animated Crop node driven by the per-frame world-space boundaries
of a selected RotoPaint / Roto / CurveTool / Crop node.

This proof of concept was "coded" live with help of Claude and ChatGPT for this video https://www.youtube.com/watch?v=lPamGg187Ac

Transforms
-----------
World-space transforms are computed via getMatrix() from Nuke's
rotopaint AnimCTransform API (see roto_to_bbox.py for implementation).

Usage
-----
1. Select a single Roto, RotoPaint, CurveTool, or Crop node.
2. Run from Script Editor, or register via register_menu().

Output
------
Creates either:
- an animated regular Crop,
- a static Crop covering the full travelled bbox area plus a helper Transform, or
- a Transform node stabilized by the bbox center followed by a static Crop,
  a post-crop Transform over black, plus an inverse matchmove Transform.

'crop' is OFF — acts as bbox carrier; enable to hard-crop.
Crop 'reformat' is ON for all generated Crop nodes.
"""

import nuke
import nuke.rotopaint as rp
import math

BVFX_DEFAULT_SHORTCUT = ""
BVFX_DEFAULT_MENULABEL = "Roto or Crop to Adjusted Crop"
BVFX_DEFAULT_SUBMENULABEL = "Convert"


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


def _get_parent_chain(shape, parent, walker_list):
    """Build list of ancestor Layers from immediate parent -> outermost (below root)."""
    chain = [parent]
    current = parent
    while current is not None and current != walker_list[0][1]:
        found = False
        for s, p in walker_list:
            if s is current:
                chain.append(p)
                current = p
                found = True
                break
        if not found:
            break
    return chain


# ---------------------------------------------------------------------------
# Shape discovery
# ---------------------------------------------------------------------------

def _get_shape_names(roto_node):
    root = roto_node['curves'].rootLayer
    results = []

    def _walk(layer, prefix):
        for item in layer:
            if isinstance(item, rp.Shape):
                display = "{}/{}".format(prefix, item.name) if prefix else item.name
                results.append((display, item))
            elif isinstance(item, rp.Layer):
                _walk(item, "{}/{}".format(prefix, item.name) if prefix else item.name)

    _walk(root, "")
    return results


# ---------------------------------------------------------------------------
# Per-frame bbox
# ---------------------------------------------------------------------------

def _shape_bbox_at_frame(shape, shapeList, frame):
    """Return (x, y, r, t) world-space pixel bbox of *shape* at *frame*.
    Uses bvfx_roto_walker-based transforms instead of manual matrix math."""
    xs, ys = [], []
    transf = shape.getTransform()

    # Find shape's immediate parent from walker list
    parent = None
    for s, p in shapeList:
        if s is shape:
            parent = p
            break

    for cp in shape:
        try:
            cx = cp.center.getPositionAnimCurve(0).evaluate(frame)
            cy = cp.center.getPositionAnimCurve(1).evaluate(frame)
        except Exception:
            continue

        # Apply shape's local transform
        local = bvfx_TTM((cx, cy), transf, frame)

        # Apply parent layer transforms up to root
        if parent is not None:
            final = bvfx_TL((local[0], local[1]), parent, frame, shapeList)
        else:
            final = local

        xs.append(final[0])
        ys.append(final[1])

    if not xs:
        return 0.0, 0.0, 0.0, 0.0

    return min(xs), min(ys), max(xs), max(ys)



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
# Crop mode helpers
# ---------------------------------------------------------------------------

CROP_MODE_REGULAR = "Regular crop"
CROP_MODE_STATIC = "Static crop"
CROP_MODE_STABILIZED = "Stabilized crop"
CROP_MODES = [CROP_MODE_REGULAR, CROP_MODE_STATIC, CROP_MODE_STABILIZED]


def _get_project_format_size():
    """Return the root format width/height as ints."""
    fmt = nuke.root().format()
    return int(fmt.width()), int(fmt.height())


def _parse_offset(value):
    """Parse user crop offset input. Empty input means 0."""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return 0
    try:
        return int(round(float(value)))
    except Exception:
        nuke.message("Offset must be a number.")
        return None


def _int_box(x, y, r, t, offset=0, frame_width=None, frame_height=None):
    """
    Convert bbox values to integer Crop.box values, apply offset, and clamp
    the result to the project image frame.

    Positive offset expands the crop. Negative offset contracts it.
    """
    x = int(math.floor(x)) - offset
    y = int(math.floor(y)) - offset
    r = int(math.ceil(r)) + offset
    t = int(math.ceil(t)) + offset

    if frame_width is not None:
        x = max(0, min(int(frame_width), x))
        r = max(0, min(int(frame_width), r))
    if frame_height is not None:
        y = max(0, min(int(frame_height), y))
        t = max(0, min(int(frame_height), t))

    # Keep a valid box even with a large negative offset.
    if r < x:
        mid = int(round((x + r) * 0.5))
        x = r = mid
    if t < y:
        mid = int(round((y + t) * 0.5))
        y = t = mid

    return x, y, r, t


def _set_static_box(crop_node, box):
    """Set Crop.box as non-animated integer values."""
    knob = crop_node['box']
    try:
        knob.clearAnimated()
    except Exception:
        pass
    for i, value in enumerate(box):
        knob.setValue(int(value), i)


def _set_animated_box(crop_node, frame_boxes):
    """Set Crop.box as animated integer values."""
    knob = crop_node['box']
    knob.setAnimated()
    for frame, box in frame_boxes:
        for i, value in enumerate(box):
            knob.setValueAt(int(value), int(frame), i)


def _enable_crop_reformat(crop_node):
    """Turn on Crop.reformat when that knob exists."""
    if 'reformat' in crop_node.knobs():
        crop_node['reformat'].setValue(True)


def _set_transform_filter_impulse(transform_node):
    """Set Transform.filter to impulse when available."""
    if 'filter' in transform_node.knobs():
        try:
            transform_node['filter'].setValue('impulse')
        except Exception:
            # Some Nuke versions expose filter as an enum index.
            try:
                transform_node['filter'].setValue(0)
            except Exception:
                pass


def _set_node_blue(node):
    """Set a node's tile colour to blue to indicate it restores image position."""
    try:
        node['tile_color'].setValue(0x2277bbff)
    except Exception:
        pass


def _set_transform_translate(transform_node, x_value, y_value, animated=False):
    """Set Transform.translate using float values, optionally clearing animation."""
    translate = transform_node['translate']
    if not animated:
        try:
            translate.clearAnimated()
        except Exception:
            pass
    translate.setValue(float(x_value), 0)
    translate.setValue(float(y_value), 1)


def _copy_translate_animation(source_transform, destination_transform):
    """Copy translate animation/static values from one Transform to another."""
    source_translate = source_transform['translate']
    destination_translate = destination_transform['translate']

    try:
        if source_translate.isAnimated():
            destination_translate.copyAnimations(source_translate.animations())
            return
    except Exception:
        pass

    try:
        destination_translate.setValue(float(source_translate.value(0)), 0)
        destination_translate.setValue(float(source_translate.value(1)), 1)
    except Exception:
        pass


def _create_static_crop_transform(input_node, static_box, source_node):
    """Create a helper Transform for Static crop, using the Crop.box x/y values.
    input_node should be the Dot placed after the Crop."""
    transform = nuke.createNode('Transform', inpanel=False)
    transform.setName('{}_Static_Transform'.format(input_node.name()))
    transform.setInput(0, input_node)
    transform['xpos'].setValue(input_node['xpos'].value() - 34)   # re-align from dot centre offset
    transform['ypos'].setValue(input_node['ypos'].value() + 95)   # below the dot
    transform['label'].setValue('static crop transform')
    _set_transform_translate(transform, int(static_box[0]), int(static_box[1]), animated=False)
    _set_transform_filter_impulse(transform)
    _set_node_blue(transform)
    return transform


def _create_matchmove_transform(stabilize_transform, input_node):
    """Create an inverse copy of the stabilization Transform for matchmove."""
    matchmove = nuke.createNode('Transform', inpanel=False)
    matchmove.setName('{}_Matchmove'.format(stabilize_transform.name()))
    matchmove.setInput(0, input_node)
    matchmove['xpos'].setValue(input_node['xpos'].value())
    matchmove['ypos'].setValue(input_node['ypos'].value() + 100)
    matchmove['label'].setValue('matchmove')

    if 'center' in stabilize_transform.knobs() and 'center' in matchmove.knobs():
        try:
            matchmove['center'].setValue(float(stabilize_transform['center'].value(0)), 0)
            matchmove['center'].setValue(float(stabilize_transform['center'].value(1)), 1)
        except Exception:
            pass

    _copy_translate_animation(stabilize_transform, matchmove)

    if 'invert_matrix' in matchmove.knobs():
        matchmove['invert_matrix'].setValue(True)

    _set_transform_filter_impulse(matchmove)
    _set_node_blue(matchmove)
    return matchmove


def _create_stabilized_post_crop_chain(crop_node, static_box):
    """
    Create the post-crop padding chain for Stabilized crop:
        Crop -> Dot("write/comp here") -> Transform -> Merge(A=Transform, B=black Constant)

    The Transform.translate uses the Crop.box x/y values and impulse filter.
    The Merge output is intended to feed the inverse matchmove Transform.
    Nodes that put the image back into position are coloured blue.
    """
    crop_x = crop_node['xpos'].value()
    crop_y = crop_node['ypos'].value()

    # Dot — placed immediately after the crop as the comp/write tap point.
    dot = nuke.createNode('Dot', inpanel=False)
    dot.setName('{}_WriteHere'.format(crop_node.name()))
    dot.setInput(0, crop_node)
    dot['xpos'].setValue(crop_x + 34)   # Dot xpos is centre; offset to align with crop
    dot['ypos'].setValue(crop_y + 100 + 5)  # +5 centres the dot on the wire
    dot['label'].setValue('write/comp here')

    post_transform = nuke.createNode('Transform', inpanel=False)
    post_transform.setName('{}_PostCrop_Transform'.format(crop_node.name()))
    post_transform.setInput(0, dot)
    post_transform['xpos'].setValue(crop_x)
    post_transform['ypos'].setValue(crop_y + 200)
    post_transform['label'].setValue('post stabilized crop transform')
    _set_transform_translate(post_transform, int(static_box[0]), int(static_box[1]), animated=False)
    _set_transform_filter_impulse(post_transform)
    _set_node_blue(post_transform)

    constant = nuke.createNode('Constant', inpanel=False)
    constant.setName('{}_Black'.format(crop_node.name()))
    constant['xpos'].setValue(crop_x + 150)
    constant['ypos'].setValue(crop_y + 200)
    if 'color' in constant.knobs():
        try:
            constant['color'].setValue(0.0, 0)
            constant['color'].setValue(0.0, 1)
            constant['color'].setValue(0.0, 2)
            constant['color'].setValue(1.0, 3)
        except Exception:
            pass

    merge = nuke.createNode('Merge2', inpanel=False)
    merge.setName('{}_Over_Black'.format(crop_node.name()))
    merge['xpos'].setValue(crop_x)
    merge['ypos'].setValue(crop_y + 300)
    if 'operation' in merge.knobs():
        try:
            merge['operation'].setValue('over')
        except Exception:
            pass

    # input A = Transform (foreground), input B = Constant (black background)
    merge.setInput(1, post_transform)
    merge.setInput(0, constant)

    return dot, post_transform, constant, merge


def _union_box(frame_boxes):
    """Return the static bbox that contains all frame bboxes."""
    xs = [box[0] for _, box in frame_boxes]
    ys = [box[1] for _, box in frame_boxes]
    rs = [box[2] for _, box in frame_boxes]
    ts = [box[3] for _, box in frame_boxes]
    return int(min(xs)), int(min(ys)), int(max(rs)), int(max(ts))


def _clamped_window_translate(frame_boxes, union_box, max_w, max_h):
    """
    For each frame compute the integer (dx, dy) Transform translation that
    centres the fixed-size window (max_w x max_h) on the frame bbox centre,
    clamped so the window never leaves the union_box boundary.

    All centres are rounded to integers so the stabilization never produces
    sub-pixel translations — the window always lands on exact pixel boundaries.

    Centre constraints (integer):
        cx in [union_x + half_w,  union_r - half_w]
        cy in [union_y + half_h,  union_t - half_h]

    When the window is the same size as the union box on an axis the allowed
    range collapses to a single point — the window is locked on that axis.

    Returns (translations, ref_cx, ref_cy) where:
        translations — list of (frame, dx, dy) as ints
        ref_cx / ref_cy — integer lock centre of the first frame
    """
    # Integer half-sizes: floor so the window never exceeds max_w/max_h.
    half_w = max_w // 2
    half_h = max_h // 2

    ux, uy, ur, ut = union_box
    cx_min = ux + half_w
    cx_max = ur - half_w
    cy_min = uy + half_h
    cy_max = ut - half_h

    def _int_centre(box):
        """Return the integer centre of a bbox, rounded to nearest pixel."""
        return (
            int(round((box[0] + box[2]) * 0.5)),
            int(round((box[1] + box[3]) * 0.5)),
        )

    # Reference: clamped integer centre on the first frame.
    first_raw_cx, first_raw_cy = _int_centre(frame_boxes[0][1])
    ref_cx = int(max(cx_min, min(cx_max, first_raw_cx)))
    ref_cy = int(max(cy_min, min(cy_max, first_raw_cy)))

    result = []
    for frame, box in frame_boxes:
        raw_cx, raw_cy = _int_centre(box)
        lock_cx = int(max(cx_min, min(cx_max, raw_cx)))
        lock_cy = int(max(cy_min, min(cy_max, raw_cy)))
        result.append((frame, ref_cx - lock_cx, ref_cy - lock_cy))
    return result, ref_cx, ref_cy

def _frame_centers(frame_boxes):
    """Return [(frame, center_x, center_y), ...] from integer bboxes."""
    centers = []
    for frame, box in frame_boxes:
        centers.append((frame, (box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5))
    return centers



def _create_stabilizer_transform(source_node, translations, ref_cx, ref_cy):
    """
    Create a Transform node using pre-computed per-frame translations.

    translations: list of (frame, dx, dy) from _clamped_window_translate.
    ref_cx / ref_cy: the lock-point centre (used as Transform.center).
    """
    transform = nuke.createNode("Transform", inpanel=False)
    transform.setName("{}_BBox_Stabilize".format(source_node.name()))
    transform.setInput(0, source_node)
    transform['xpos'].setValue(source_node['xpos'].value() + 150)
    transform['ypos'].setValue(source_node['ypos'].value() + 80)
    transform['label'].setValue("BBox stabilization (clamped window)")

    if 'center' in transform.knobs():
        transform['center'].setValue(float(ref_cx), 0)
        transform['center'].setValue(float(ref_cy), 1)

    translate = transform['translate']
    translate.setAnimated()
    for frame, dx, dy in translations:
        translate.setValueAt(dx, int(frame), 0)
        translate.setValueAt(dy, int(frame), 1)

    _set_transform_filter_impulse(transform)
    return transform
def _ask_bbox_options(shapes):
    """Show one single dialog containing shape, crop mode, and offset options."""
    shape_names = [name for name, _shape in shapes]
    panel = nuke.Panel("Roto BBox Crop Options")
    panel.addEnumerationPulldown("Crop mode", " ".join(["{%s}" % m for m in CROP_MODES]))
    panel.addSingleLineInput("Offset pixels", "0")
    panel.addButton("Cancel")
    panel.addButton("Create")
    if not panel.show():
        return None
    selected_mode = panel.value("Crop mode")
    offset = _parse_offset(panel.value("Offset pixels"))

    if offset is None:
        return None

    if selected_mode not in CROP_MODES:
        selected_mode = CROP_MODE_REGULAR

    return selected_mode, offset


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def main():
    selected = nuke.selectedNodes()
    if len(selected) != 1:
        nuke.message("Please select exactly one Roto, RotoPaint or Crop node.")
        return

    roto_node = selected[0]
    if roto_node.Class() not in ("Roto", "RotoPaint", "CurveTool", "Crop"):
        nuke.message("'{}' is not a supported node type (Roto, RotoPaint, CurveTool, Crop).".format(roto_node.name()))
        return

    is_crop_input = roto_node.Class() == "Crop"
    is_curve_input = roto_node.Class() == "CurveTool"

    if not (is_crop_input or is_curve_input):
        shapes = _get_shape_names(roto_node)
        if not shapes:
            nuke.message("No shapes found inside '{}'.".format(roto_node.name()))
            return

        options = _ask_bbox_options(shapes)
        if options is None:
            return
        crop_mode, offset_value = options
    else:
        panel = nuke.Panel("Crop BBox Options")
        panel.addEnumerationPulldown("Crop mode", " ".join(["{%s}" % m for m in CROP_MODES]))
        panel.addSingleLineInput("Offset pixels", "0")
        panel.addButton("Cancel")
        panel.addButton("Create")

        if not panel.show():
            return

        crop_mode = panel.value("Crop mode")
        offset_value = _parse_offset(panel.value("Offset pixels"))
        if offset_value is None:
            return

        source_type = "CurveTool" if is_curve_input else "Crop"
        shape_name = "Source: {} animation".format(source_type)
        shape = None

    first = int(nuke.root()['first_frame'].value())
    last  = int(nuke.root()['last_frame'].value())
    total = last - first + 1
    frame_width, frame_height = _get_project_format_size()

    frame_boxes = []

    if not is_crop_input and not is_curve_input:
        shapeList = bvfx_roto_walker(roto_node)

    task = nuke.ProgressTask("Sampling roto bbox…")
    try:
        for i, frame in enumerate(range(first, last + 1)):
            if task.isCancelled():
                nuke.message("Cancelled.")
                return
            task.setProgress(int(100.0 * i / total))
            task.setMessage("Frame {}".format(frame))

            if is_crop_input or is_curve_input:
                knob_name = "autocropdata" if is_curve_input else "box"
                x = roto_node[knob_name].valueAt(frame, 0)
                y = roto_node[knob_name].valueAt(frame, 1)
                r = roto_node[knob_name].valueAt(frame, 2)
                t = roto_node[knob_name].valueAt(frame, 3)
            else:
                x, y, r, t = compute_baked_bbox_at_frame(shapeList, frame)

            box = _int_box(
                x, y, r, t,
                offset=offset_value,
                frame_width=frame_width,
                frame_height=frame_height,
            )
            frame_boxes.append((frame, box))
    finally:
        del task

    output_input = roto_node
    transform_node = None

    # For stabilized mode, compute everything before creating nodes.
    stabilized_union_box    = None
    stabilized_window_size  = None  # (max_w, max_h)
    stabilized_translations = None  # [(frame, dx, dy), ...]
    stabilized_ref_cx       = None
    stabilized_ref_cy       = None

    if crop_mode == CROP_MODE_STABILIZED:
        # The union box is the hard spatial boundary (static crop area).
        # It is already clamped to the frame by _int_box during sampling.
        stabilized_union_box = _union_box(frame_boxes)

        # Fixed window size = largest single-frame bbox.
        max_w = max(box[2] - box[0] for _, box in frame_boxes)
        max_h = max(box[3] - box[1] for _, box in frame_boxes)
        stabilized_window_size = (max_w, max_h)

        # Per-frame translations that slide the window inside the union box.
        stabilized_translations, stabilized_ref_cx, stabilized_ref_cy = \
            _clamped_window_translate(frame_boxes, stabilized_union_box, max_w, max_h)

        transform_node = _create_stabilizer_transform(
            roto_node, stabilized_translations,
            stabilized_ref_cx, stabilized_ref_cy            
        )
        transform_node['xpos'].setValue(roto_node['xpos'].value())
        transform_node['ypos'].setValue(roto_node['ypos'].value() + 100)
        output_input = transform_node

    crop_node = nuke.createNode("Crop", inpanel=False)
    suffix = {
        CROP_MODE_REGULAR: "BBox",
        CROP_MODE_STATIC: "BBox_Static",
        CROP_MODE_STABILIZED: "BBox_Stabilized_Crop",
    }[crop_mode]
    crop_node.setName("{}_{}".format(roto_node.name(), suffix))
    crop_node['label'].setValue(
        "{}\nBBox: {}\nOffset: {} px".format(
            crop_mode, roto_node.name(), offset_value
        )
    )
    crop_node['crop'].setValue(False)
    _enable_crop_reformat(crop_node)
    crop_node.setInput(0, output_input)
    crop_node['xpos'].setValue(output_input['xpos'].value())
    crop_node['ypos'].setValue(output_input['ypos'].value() + 100)

    extra_transform_node = None
    post_crop_transform = None
    black_constant = None
    merge_node = None

    dot_node = None  # "write/comp here" tap point after the crop

    if crop_mode == CROP_MODE_REGULAR:
        _set_animated_box(crop_node, frame_boxes)
    elif crop_mode == CROP_MODE_STATIC:
        static_box = _union_box(frame_boxes)
        _set_static_box(crop_node, static_box)
        # Dot below the crop as a write/comp tap point.
        dot_node = nuke.createNode('Dot', inpanel=False)
        dot_node.setName('{}_WriteHere'.format(crop_node.name()))
        dot_node.setInput(0, crop_node)
        dot_node['xpos'].setValue(crop_node['xpos'].value() + 34)
        dot_node['ypos'].setValue(crop_node['ypos'].value() + 100 + 5)
        dot_node['label'].setValue('write/comp here')
        extra_transform_node = _create_static_crop_transform(dot_node, static_box, roto_node)
    elif crop_mode == CROP_MODE_STABILIZED:
        # Static crop box: window-sized, positioned at the first-frame lock centre.
        max_w, max_h = stabilized_window_size
        half_w = max_w * 0.5
        half_h = max_h * 0.5
        win_x = int(math.floor(stabilized_ref_cx - half_w))
        win_y = int(math.floor(stabilized_ref_cy - half_h))
        win_r = int(math.ceil(stabilized_ref_cx + half_w))
        win_t = int(math.ceil(stabilized_ref_cy + half_h))
        window_box = (win_x, win_y, win_r, win_t)
        _set_static_box(crop_node, window_box)
        if transform_node is not None:
            dot_node, post_crop_transform, black_constant, merge_node =                 _create_stabilized_post_crop_chain(crop_node, window_box)
            extra_transform_node = _create_matchmove_transform(transform_node, merge_node)
    created_nodes = []
    if transform_node is not None:
        created_nodes.append(transform_node.name())
    created_nodes.append(crop_node.name())
    if dot_node is not None:
        created_nodes.append(dot_node.name())
    if post_crop_transform is not None:
        created_nodes.append(post_crop_transform.name())
    if black_constant is not None:
        created_nodes.append(black_constant.name())
    if merge_node is not None:
        created_nodes.append(merge_node.name())
    if extra_transform_node is not None:
        created_nodes.append(extra_transform_node.name())
    created = " + ".join(created_nodes)

    return crop_node


if __name__ == "__main__":
    main()
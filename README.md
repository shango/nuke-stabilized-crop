# StabilizedCrop

A Nuke node that stabilizes and crops a fixed resolution window around a rotod
element, for round-tripping through an AI inpainting model, then puts the result
back over the plate.

Built for ComfyUI round trips, where the model needs an exact input resolution
(1024x1024, 832x1216, and so on) rather than "bbox plus some padding".

```
in 0 plate ─┬─ AlphaCopy ── Stabilize ── CropWindow ──────────┐
in 1 roto  ─┘   (matte→α)   (int trans)  (res_w x res_h)      ├─ OutSwitch ─ out
            └─ MatteGrow ─ MatteBlur ─┐                       │
in 2 result ─── ResultPlace ─ Matchmove ─ CompMerge ───────────┘
```

The crop -> comp round trip is **pixel exact**. Every transform in both
directions is an integer pixel translation with an impulse filter, so no
resampling happens in either direction.

Requires Nuke 15.x / 16.x (tested against 16.0v8, Python 3.11). Likely fine on
13.x / 14.x.

## Files

| file | purpose |
|---|---|
| `stabilized_crop.py` | **The tool. Self-contained - this is the only file you deploy.** |
| `tests/test_solve.py` | Offline verification of the window solve and the round trip. Runs without Nuke. |
| `reference/roto_to_bbox.py` | The original proof of concept this grew out of. Not imported by the tool; kept for provenance and because it still works standalone. |

The world-space roto bbox math (`bvfx_roto_walker`, `bvfx_TTM`, `bvfx_TL`,
`compute_baked_bbox_at_frame`) is inlined verbatim into `stabilized_crop.py` so
it ships as one file. If you fix that math in `reference/roto_to_bbox.py`, port
it across by hand.

Keep the filename `stabilized_crop.py` as it is. The node's buttons import the
module by that name, so renaming it breaks every node already saved in a script.

## Install

### Shared / facility path

Nuke runs `init.py` then `menu.py` in every directory on the plugin path, and
`nuke.pluginAddPath()` resolves relative paths against the `init.py` that calls
it while also adding them to `sys.path`.

```
<shared>/
├── .nuke/
│   ├── init.py      add:  nuke.pluginAddPath('../python')
│   └── menu.py      add:  import stabilized_crop
│                          stabilized_crop.register_menu("MyStudio/Roto")
└── python/
    └── stabilized_crop.py
```

Adjust `'../python'` to `'./python'` if the directory on `NUKE_PATH` is the
parent rather than the `.nuke` subfolder. `register_menu()` accepts a nested
menu path and an optional keyboard shortcut:

```python
stabilized_crop.register_menu("MyStudio/Roto", shortcut="ctrl+alt+s")
```

Menu registration must go in `menu.py`, not `init.py` - `init.py` also runs in
`nuke -t`, where there is no menu.

**Do not use a `python/` subfolder if you skip the `pluginAddPath` line.** A
`.py` module sitting in a `NUKE_PATH` root is not guaranteed to be importable:
Nuke's plugin loader finds `init.py`/`menu.py` by its own mechanism, which is not
Python's import path.

### Single user

Drop `stabilized_crop.py` into `~/.nuke` (`C:\Users\<you>\.nuke` on Windows) and
restart. That directory is on the plugin path automatically. Either add the
`register_menu()` call to `~/.nuke/menu.py`, or just launch it from the Script
Editor:

```python
import stabilized_crop
stabilized_crop.create()
```

Note that `~/.nuke` is **always prioritised over facility paths**, by design, so
a stale personal copy will silently shadow a shared release.

### Why it must be importable

The node's buttons store the literal string `import stabilized_crop`, executed
later in a fresh context. Pasting the file body into the Script Editor puts the
code in `__main__`, not in a module named `stabilized_crop`, so the node will
build and then every button will fail with `ModuleNotFoundError`.

Renders are unaffected either way: the solve is baked into the internal Transform
curves and the Crop box, so a saved script comps correctly with **no Python
module present at all**. Nothing to install on render nodes.

## Use

1. Select the plate and the Roto, then create the node. It wires them correctly
   regardless of selection order (a Roto/RotoPaint always lands on input 1).
   Manually: **0 = plate, 1 = roto, 2 = result**.
2. Set the frame **range**, press **Analyze roto**. It samples the bbox from the
   roto control points, with no rendering, and reports
   `max bbox: W x H   travel: W x H`.
3. Pick a **preset** or type a **resolution**. This re-solves instantly from the
   cached bbox, so you can dial it in while watching the report.
4. Leave **mode = crop** and check it in the viewer. Output is exactly your
   WxH, with the roto matte in alpha.
5. Hang a **Write** off it. For a separate mono mask sequence, add a **Shuffle**
   (alpha -> rgb) and a second Write.
6. Render, run it through ComfyUI.
7. **Read** the result back, plug it into input **2**, set **mode = comp**. You
   get the result back in plate space, comped through the roto matte.

If the report shows `! bbox clipped on N of M frames`, your element is larger
than the crop on those frames. **Set res to fit bbox** rounds up to the next
multiple of 8 that contains it.

## Knobs

| knob | what it does |
|---|---|
| `range` first / last | frames to sample the roto over |
| `Analyze roto` | sample the bbox and cache it on the node. Re-run after editing roto shapes. |
| `preset` | common model resolutions; sets the two fields below |
| `resolution` w / h | crop size in pixels. The plate is **not** rescaled; this is the size of the window cut out of it. |
| `Set res to fit bbox` | round up to the next multiple of 8 that contains the largest bbox |
| `mode` | `crop` renders to ComfyUI, `comp` puts the result back over the plate |
| `matte grow` | dilate the comp-back matte, in pixels. Negative shrinks. |
| `matte blur` | soften the comp-back matte edge |

The per-frame bbox is cached on hidden animated knobs (`bbox_lo`, `bbox_hi`) and
saved in the `.nk`, which is why changing resolution does not re-sample.

## Design notes

**Fixed resolution, native scale.** The crop is a window of real plate pixels at
1:1. Nothing is rescaled, which is what keeps the round trip lossless. The
tradeoff is that if your element is much smaller than the target resolution, the
model spends most of its pixels on surrounding plate.

**Edge handling.** The window is clamped to stay inside the plate, so near frame
edges the element drifts within the crop rather than pulling in off-plate black.
If the requested resolution is larger than the plate on an axis, the window is
centred instead and the report warns that edges will be black.

**Reference frame.** The first analyzed frame's window becomes the static Crop
box. Every other frame is translated so its window lands on that same box.

## Verifying

```
python3 tests/test_solve.py
```

No Nuke needed - the geometry functions (`_solve_windows`, `_bbox_extremes`) have
no Nuke dependency, so the test stubs `nuke` and `nuke.rotopaint`. The property
that matters is that plate pixel -> stabilize -> crop -> ResultPlace ->
matchmove returns the identical integer coordinate. That is checked across
interior frames, frames clamped at the plate origin and maximum, oversize bboxes,
and resolutions larger than the plate.

### Still to confirm inside Nuke

The geometry is verified, but four Nuke API details were written from the docs
without a running Nuke to test against. Each fails loudly and each is a one-line
fix:

| symptom | fix |
|---|---|
| crop output has no alpha | swap the `Copy` inputs in `_build_internals` (`inputs=[plate, roto]` -> `[roto, plate]`) |
| node creation throws on `CompMerge` | a `Merge2` knob name is wrong: `output`, `bbox`, or `maskChannelInput` |
| `matte grow` shrinks instead of grows | negate the `Dilate` size expression |
| resolution knobs stop updating live | `knobChanged` is not firing; press **Analyze roto** to re-solve |

## Credits

The world-space roto bbox math comes from the `roto_to_bbox.py` proof of concept,
coded live with Claude and ChatGPT for
[this video](https://www.youtube.com/watch?v=lPamGg187Ac).

# StabilizedCrop

A Nuke node that stabilizes and crops a fixed-resolution window around a rotod
element, so you can send it out to an AI inpainting model and comp the result
back over your plate.

Built for ComfyUI round trips, where the model wants an exact input size
(1024x1024, 832x1216) rather than "bbox plus some padding".

```
in 0 plate ─┬─ AlphaCopy ─ Stabilize ─ CropWindow ────────────┐
in 1 roto  ─┤  (matte→α)   (int trans)  (res_w x res_h)       │
            │                                                 ├─ OutSwitch ─ out
            └─ MatteSource ─ MatteGrow ─ MatteBlur ─┐         │
               (roto | plate α)                     ↓         │
in 2 result ─── ResultPlace ─── Matchmove ───── CompMerge ─────┘
```

The round trip is pixel exact. Every move in both directions is an integer
translation with an impulse filter, so nothing ever gets resampled.

Nuke 15.x / 16.x. Probably fine on 13.x / 14.x.

## Install

Copy the `stabilized_crop/` folder to wherever your studio keeps its Nuke stuff,
say `S:\nuke\`. It carries its own `init.py` and `menu.py`, so it just needs to
be on the plugin path. `stabilized_crop.zip` in this repo holds that folder plus
this README, if you need to sneakernet it somewhere. Rebuild it after changing
the tool:

```
rm -f stabilized_crop.zip
zip -r -X stabilized_crop.zip stabilized_crop README.md -x '*__pycache__*'
```

Restart Nuke and see if it got picked up for free - some launchers add every
subfolder of the share:

```python
import stabilized_crop
print(stabilized_crop.__file__)
```

If that throws, Nuke needs telling. It runs `init.py` and `menu.py` in each
directory literally on the plugin path and does not recurse, so add one line to
any `init.py` that already runs - the share's, or your own `~/.nuke/init.py`
(`C:\Users\<you>\.nuke\init.py`, create it if missing):

```python
nuke.pluginAddPath('S:/nuke/stabilized_crop')
```

Not sure which `init.py` already runs? This tells you:

```python
import os, nuke
print("NUKE_PATH:", os.environ.get("NUKE_PATH"))
print("HOME:     ", os.environ.get("HOME"))
for path in nuke.pluginPath():
    print("   ", path)
```

Watch for `HOME` pointing at the share. If it does, `~/.nuke` is a shared
directory and one line there covers everyone. Set `MENU_PATH` in `menu.py` to
put the tool wherever your menus live.

Two things worth knowing. Keep the filename `stabilized_crop.py` - the node's
buttons import it by name, so renaming it breaks every node already saved.
And pasting the file into the Script Editor is not the same as installing it:
the code lands in `__main__`, the node builds, then every button fails with
`ModuleNotFoundError`. Renders don't care either way, since the solve is baked
into the internal knobs. Nothing to install on render nodes.

## Use

1. Select your plate and roto, make the node. It sorts out which is which.
   By hand: **0 = plate, 1 = roto, 2 = result**.
2. Set the **range**, press **Analyze roto**. It reads the bbox straight off the
   control points, no rendering, and tells you `max bbox` and `travel`.
3. Pick a **preset** or type a **resolution**. Re-solves instantly off the
   cached bbox, so you can dial it in while watching the report.
4. Leave **mode = crop** and look at it. Output is exactly your WxH, roto matte
   in alpha.
5. Hang a **Write** off it. Want a separate mono mask? Add a **Shuffle**
   (alpha -> rgb) and a second Write.
6. Render, run it through ComfyUI.
7. **Read** the result back into input **2**, set **mode = comp**. It lands back
   in plate space, comped through the roto matte.

`! bbox clipped on N of M frames` means your element is bigger than the crop
there. **Set res to fit bbox** rounds up to the next multiple of 32.

## Multiple roto shapes

Merging two Roto nodes and feeding in the Merge doesn't work - you'll get
`'Merge1' is a Merge - the roto input needs a Roto or RotoPaint`. The sampler
never renders anything, it reads shapes straight off the node:

```python
rotoRoot = rotoNode["curves"].rootLayer
```

A Merge has no `curves` knob, so there's nothing to read. That's the price of
Analyze being instant and resolution-independent. Chaining Roto2 into Roto1
doesn't help either - you get a combined *image*, but Roto1 still only knows
about its own shapes.

**Put all the shapes in one Roto node instead.** The walker recurses the whole
layer tree, so one node holding ten shapes across nested layers already gives
you a combined bbox. Select the shapes in one Roto's curve list, copy, paste
into the other. They keep their own animation and per-shape transforms.

Two things to watch:

- **Everything in the node counts.** There's no way to exclude a shape, so a
  garbage matte sitting in the same node will inflate your crop. Keep the bbox
  roto clean and put anything that shouldn't drive it elsewhere.
- **Node-level transforms don't travel.** A transform on the source Roto's root
  layer or Transform tab stays behind when you copy shapes out. Per-shape
  transforms are fine.

If your shapes genuinely have to live in separate nodes, the two ways forward
are more roto inputs unioned at sample time, or measuring a rendered alpha
instead - which would work with any source but makes Analyze a real render.
Neither is built; ask if you need one.

## Knobs

| knob | what it does |
|---|---|
| `range` first / last | frames to sample the roto over |
| `Analyze roto` | samples the bbox and caches it, with the plate format. Needs plate and roto connected. Re-run after editing shapes. |
| `preset` | common model resolutions |
| `resolution` w / h | crop size. The plate is **not** rescaled; this is the size of the window cut out of it. |
| `Set res to fit bbox` | round up to a multiple of `RES_STEP` (32) that contains the largest bbox |
| `offset` x / y | shift the crop window, in pixels. Applied before the plate clamp, so it can't drag in black. Animatable. |
| `mode` | `crop` goes out to ComfyUI, `comp` brings it back |
| `use plate alpha` | comp mode only. Mask the comp back with the plate's own alpha instead of the roto. |
| `matte grow` | dilate the comp-back matte. Negative shrinks. |
| `matte blur` | soften its edge |

## Setting a node aside

Render the crop, unplug everything, come back in a month. Nothing needs
re-analyzing.

Comp mode never reads the bbox cache - it runs off the baked `Matchmove` curve
and crop box, which save with the script like any other knob. You could delete
the roto and the comp would still line up. Changing `mode` doesn't re-solve.

Re-analyzing is deterministic: same roto, same range, same resolution, same
plate gives bit-identical curves. Plug in a *different* plate and the report
says so rather than quietly moving your bake:

```
! plate is 4096 x 2160 but was analyzed at 1920 x 1080 - press Analyze roto
```

## Versions

Nodes carry the version that built them, at the bottom of the panel. What's
installed is `stabilized_crop.__version__`. They can differ, and usually that's
fine - the number tells you whether it matters:

- **patch** - behaviour fixes. Old nodes pick these up automatically, since the
  buttons import the module at click time. Just deploy.
- **minor** - new or changed knobs and internals. Old nodes keep what they were
  built with and need rebuilding.
- **major** - a public function or the file itself got renamed. Saved nodes break.

Tags on this repo match, so `v1.2.0` is a diff you can read.

## Notes

**Native scale.** The crop is real plate pixels at 1:1, never rescaled, which is
what keeps the round trip lossless. The tradeoff is that a small element leaves
the model spending most of its pixels on surrounding plate.

**Stride.** Latent models downsample 8x to the latent and most UNets another 8x,
so off-32 sizes get padded or rejected. Every preset is a multiple of 64 and the
fit button rounds to `RES_STEP`. The resolution fields themselves are free - type
1000 and you get 1000.

**Edges.** The window slides inward to stay on the plate, so near frame edges the
element drifts within the crop instead of dragging in off-plate black. Ask for a
resolution bigger than the plate and it centres instead, and warns you.

**Offset.** Use it when the bbox centre isn't where you want the element sitting
in frame - more headroom, or the subject deliberately off-centre. It's applied
before the plate clamp, so it can't pull in black either, and if a plate edge is
eating some of it you get `! offset limited by plate edge on N of M frames`
rather than a knob that silently does nothing. Animate it if you want the
framing to drift; the round trip stays pixel exact because the same window
position drives both directions.

**Plate size is cached, not read live.** It's an input to the solve, since the
window clamps to it. `_apply` runs on `inputChange`, so reading it live meant
that pulling the plate pipe fell back to the project format and silently rebaked
everything. Analyze records it; nothing else touches it.

**Reference frame.** The first analyzed frame's window becomes the static crop
box. Every other frame is translated onto it.

## Tests

```
python3 tests/test_solve.py
```

Runs without Nuke - the geometry has no Nuke dependency, so the test stubs it
out. It checks the thing that actually matters: plate pixel -> stabilize -> crop
-> ResultPlace -> matchmove comes back to the same integer coordinate. Interior
frames, both clamped edges, oversize bboxes, resolutions bigger than the plate,
and the plate size cache.

### Not yet run inside Nuke

The geometry is verified, but a few API details came from the docs without a
running Nuke to check against. Each fails loudly and each is a one-line fix:

| symptom | fix |
|---|---|
| crop output has no alpha | swap the `Copy` inputs in `_build_internals` |
| `matte grow` / `matte blur` do nothing | the `CompMerge` mask knob is `maskChannelInput`, not `maskChannelMask`. Fixed in v1.3.0 - see below. |
| `matte grow` shrinks | negate the `Dilate` size expression |
| `use plate alpha` inverted | swap the `MatteSource` switch inputs (`[roto, plate]`) |
| resolution stops updating live | `knobChanged` isn't firing; press **Analyze roto** |

### Repairing a node built before v1.3.0

`matte grow` and `matte blur` did nothing on nodes built by v1.2.0 or earlier.
`CompMerge` was masking off its B input's alpha rather than its mask input, so
the whole grow/blur branch was wired up and never read. It looked plausible,
because B is the plate.

Rebuilding the node fixes it. To repair one in place instead, select it and run:

```python
import stabilized_crop as sc
merge = sc._child(nuke.selectedNode(), "CompMerge")
merge["maskChannelMask"].setValue("rgba.alpha")
merge["maskChannelInput"].setValue("none")
```

## Files

```
stabilized_crop/            the deployable folder
├── init.py                 puts ./python on the plugin path
├── menu.py                 menu entry. Set MENU_PATH here.
└── python/stabilized_crop.py

tests/test_solve.py         offline checks
reference/roto_to_bbox.py   the proof of concept this grew out of
```

The roto bbox math is inlined verbatim into `stabilized_crop.py` so it ships as
one file. Fix it in `reference/` and you have to port it across by hand.

## Credits

The world-space roto bbox math comes from the `roto_to_bbox.py` proof of concept,
coded live with Claude and ChatGPT for
[this video](https://www.youtube.com/watch?v=lPamGg187Ac).

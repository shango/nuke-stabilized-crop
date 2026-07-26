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

```
stabilized_crop/            <- the deployable folder. Copy this whole thing.
├── init.py                 adds ./python to the plugin path and sys.path
├── menu.py                 registers the menu entry. Edit MENU_PATH here.
└── python/
    └── stabilized_crop.py  the tool itself, self-contained

tests/test_solve.py         offline verification. Runs without Nuke.
reference/roto_to_bbox.py   the proof of concept this grew out of. Not imported.
```

The world-space roto bbox math (`bvfx_roto_walker`, `bvfx_TTM`, `bvfx_TL`,
`compute_baked_bbox_at_frame`) is inlined verbatim into `stabilized_crop.py` so
it ships as one file. If you fix that math in `reference/roto_to_bbox.py`, port
it across by hand.

Keep the filename `stabilized_crop.py` as it is. The node's buttons import the
module by that name, so renaming it breaks every node already saved in a script.

## Install

The `stabilized_crop/` folder is a self-contained Nuke plugin directory. It
carries its own `init.py` and `menu.py`, so **nothing needs to be added to any
shared startup file.** Nuke runs `init.py` then `menu.py` in every directory on
the plugin path, and `init.py` here does the one thing that matters:

```python
nuke.pluginAddPath('./python')
```

Nuke resolves that relative path against the `init.py` calling it, and
`pluginAddPath` also adds it to `sys.path`, which is what makes
`import stabilized_crop` resolve for the node's button callbacks.

### 1. Copy the folder to the share

```
S:\nuke\
├── gizmos\
├── toolsets\
└── stabilized_crop\        <- copy the folder from this repo here
    ├── init.py
    ├── menu.py
    └── python\
        └── stabilized_crop.py
```

`stabilized_crop.zip` in this repo holds exactly that folder and nothing else,
for carrying to a machine that cannot reach GitHub. Unzip it directly into
`S:\nuke\`. Rebuild it after changing the tool:

```
rm -f stabilized_crop.zip
zip -r -X stabilized_crop.zip stabilized_crop -x '*__pycache__*'
```

### 2. Get that folder onto the plugin path

**Try the free option first.** Some studio launchers glob the subfolders of the
share and add each one, which is one way `gizmos\` and `toolsets\` load with no
startup file present. If yours does, step 1 was the whole install. Restart Nuke
through the launcher and check:

```python
import nuke
for p in nuke.pluginPath():
    if 'stabilized' in p.lower():
        print("caught:", p)
import stabilized_crop
print(stabilized_crop.__file__)
```

If both print, stop here. Nuke itself does **not** recurse into subdirectories of
the plugin path - it runs `init.py` then `menu.py` in each directory that is
literally on the path - so this only works if the launcher does the globbing.

If nothing prints, run this in the Script Editor of a session started the normal
way. Whatever wrapper the studio launches Nuke through is what sets the
environment, and this reports what it did:

```python
import os
import nuke

print("NUKE_PATH:", os.environ.get("NUKE_PATH"))
print("HOME:     ", os.environ.get("HOME"))
print("plugin path:")
for path in nuke.pluginPath():
    print("   ", path)
```

Then match the output to one of the cases below. They are ordered by how little
work they are, not by preference - any of them works.

#### If the share root itself is on the path

For example the plugin path contains `S:/nuke`. Create `S:\nuke\init.py`:

```python
import nuke

nuke.pluginAddPath('./stabilized_crop')
```

Calling `pluginAddPath` from inside an `init.py` executes the `init.py` in the
directory being added, so this chains into `stabilized_crop/init.py`, which adds
`./python`. The tool's `menu.py` runs in the later menu pass.

Check whether `S:\nuke\init.py` already exists first, and **append** to it rather
than overwriting. It is a shared facility file: every artist picks up the change
at their next launch.

#### If HOME points at the share

For example `HOME: S:\nuke`. Then `~/.nuke` **is** `S:\nuke\.nuke`, and that one
directory is every artist's Nuke home at once. Create `S:\nuke\.nuke\init.py`
(or append to it) with:

```python
nuke.pluginAddPath('S:/nuke/stabilized_crop')
```

One file, everyone gets it, no environment variable to change. Forward slashes
are fine on Windows and avoid backslash escaping.

#### If NUKE_PATH lists the share's folders

Append this folder to it wherever the launcher sets it. Semicolon separated on
Windows, colon on Linux/macOS:

```
NUKE_PATH=S:\nuke\gizmos;S:\nuke\toolsets;S:\nuke\stabilized_crop
```

Alternatively, if any directory already on `NUKE_PATH` has an `init.py` you can
edit, add a `pluginAddPath` line there. A relative path resolves against that
`init.py`, so from a sibling directory:

```python
nuke.pluginAddPath('../stabilized_crop')
```

#### If you cannot edit the launcher, or want to test first

Add the same one-liner to your own `~/.nuke/init.py`
(`C:\Users\<you>\.nuke\init.py`, create the file if it does not exist):

```python
nuke.pluginAddPath('S:/nuke/stabilized_crop')
```

`~/.nuke` is always on the plugin path, so this needs no environment variable and
no admin rights. The cost is one line per workstation. Note that if `HOME` is
redirected to the share, this is the same file as the first case - check the
diagnostic output rather than assuming it is local.

### 3. Set the menu location

Edit the two constants at the top of `stabilized_crop/menu.py`:

```python
MENU_PATH = "Convert"      # nests with slashes, e.g. "MyStudio/Roto"
SHORTCUT = ""              # e.g. "ctrl+alt+s"
```

Restart Nuke. The tool appears in the Nodes menu at `MENU_PATH`, and by Tab
search.

### Verifying it loaded

```python
import stabilized_crop
print(stabilized_crop.__file__)
```

If that raises `ModuleNotFoundError`, step 2 did not take effect. Check
`nuke.pluginPath()` for the folder.

### Single user, no share

Copy just `python/stabilized_crop.py` into `~/.nuke`
(`C:\Users\<you>\.nuke`) and launch it from the Script Editor:

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
multiple of 32 that contains it.

## Knobs

| knob | what it does |
|---|---|
| `range` first / last | frames to sample the roto over |
| `Analyze roto` | sample the bbox and cache it, with the plate format, on the node. Needs both plate and roto connected. Re-run after editing roto shapes. |
| `preset` | common model resolutions; sets the two fields below |
| `resolution` w / h | crop size in pixels. The plate is **not** rescaled; this is the size of the window cut out of it. |
| `Set res to fit bbox` | round up to the next multiple of `RES_STEP` (32) that contains the largest bbox |
| `mode` | `crop` renders to ComfyUI, `comp` puts the result back over the plate |
| `matte grow` | dilate the comp-back matte, in pixels. Negative shrinks. |
| `matte blur` | soften the comp-back matte edge |

The per-frame bbox is cached on hidden animated knobs (`bbox_lo`, `bbox_hi`),
and the plate format it was solved against on `plate_size`. All three are
`INVISIBLE`, not `DO_NOT_WRITE`, so they save in the `.nk` and come back on load.
That is why changing resolution does not re-sample.

## Setting a node aside between crop and comp

You can render the crop, disconnect everything, and come back weeks later.
Nothing needs to be re-analyzed.

Comp mode does not read the bbox cache at all. It renders through the baked
`Matchmove` curve and the static crop box, which are ordinary internal node
knobs saved with the script. The roto could be deleted entirely and the comp
would still line up. Switching `mode` never triggers a re-solve.

A re-solve happens only on `res_preset`, `resolution`, `range`, or an input
change, and since the plate format is cached it produces identical numbers to
the ones you rendered against. If you reconnect a **different** plate, the report
says so and the bake is left alone until you press **Analyze roto**:

```
! plate is 4096 x 2160 but was analyzed at 1920 x 1080 - press Analyze roto
```

Re-analyzing is deterministic. Same roto, same range, same resolution and same
plate format gives bit-identical curves - control points are evaluated per
frame, then floored and ceiled to integers. The one input that can drift
unnoticed is `range`, which is initialized from the project range when a node is
built, so a *new* node made after the shot range changed will differ from an old
one.

## Design notes

**Fixed resolution, native scale.** The crop is a window of real plate pixels at
1:1. Nothing is rescaled, which is what keeps the round trip lossless. The
tradeoff is that if your element is much smaller than the target resolution, the
model spends most of its pixels on surrounding plate.

**Resolution stride.** Latent diffusion models work on an 8x downsampled latent
and most UNets downsample a further 8x, so dimensions off a multiple of 32 get
silently padded or rejected. Every preset is a multiple of 64, and
**Set res to fit bbox** rounds to `RES_STEP` (32, set at the top of
`stabilized_crop.py`). The `resolution` fields themselves are not constrained -
if you type an arbitrary number, that is what you get.

**Plate size is cached, not read live.** The window is clamped to the plate, so
the plate's format is an input to the solve. It is recorded when you press
Analyze and read from that cache afterwards. Reading it live would mean that
unplugging the plate falls back to the project format and silently re-bakes the
transforms against the wrong numbers - `_apply` runs on `inputChange`, so this
needed no user action beyond pulling a pipe. Analyze refuses to run without a
plate connected, so a project-format guess can never enter the cache.

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

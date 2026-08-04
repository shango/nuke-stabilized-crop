# Stabilized Crop

A Nuke node for prepping shots that are going out to an AI inpainting model,
and for bringing the results back home again.

## Why you want it

For any AI inpainting task, the first step is generally to roto the object
you're replacing or altering, because the model needs that matte to know what
it's working on.

The trouble is that handing a model a full 4K plate to fix one small area is a
waste. It gets distracted, it gets overloaded, and the bit you actually care
about ends up with only a handful of pixels devoted to it. Models also expect
specific input sizes, like 1024x1024, and you rarely get to choose.

A stabilized crop solves both. You cut a fixed-size window out of the plate that
follows your element around, so the model sees nothing but the area you're
altering, at a resolution it likes, and your subject stays put in frame instead
of flying around.

This node builds that crop off the bounding box of your roto, then reverses the
whole thing later to put the result back exactly where it came from.

The round trip is lossless. Everything moves in whole pixels with no filtering,
so the pixels that come back are the pixels that left.

It does those two things and nothing else. It doesn't handle your matte and it
doesn't comp anything. Pixels go through untouched in both directions, so what
the crop delivers is your plate and what the uncrop delivers is your patch,
sitting back where it came from for you to merge yourself.

## To use

Select the plate, select the roto node, then create the Stabilized Crop node and
it will hook everything up for you. Order doesn't matter, it works out which is
which. You can also wire it by hand: **input 0 is the plate, input 1 is the
roto, input 2 is the result** you'll get back later.

The roto is only ever read by **Analyze roto**, to find the bounding box. It
never touches the picture.

Then:

1. Make sure the **range** fields cover your full clip, and click
   **Analyze roto**. It reads the bounding box off your roto shapes, which takes
   no time at all because nothing has to render.
2. Pick a **preset** that makes sense. Often the bbox alone isn't enough
   padding, so go a size up and give the model some context to work with.
3. Use the **offset** controls to move the crop around if you need to.
4. Add a Write node and you're set to deliver the crop.

For the mask that goes out alongside it, copy and paste the node, and plug your
roto into the copy's **plate** input instead of the plate. Everything is already
solved on the pasted node, so you get the same crop of your matte, lined up with
the picture exactly. Write that out to a second path.

Later, when the patch comes back with the AI generated effect:

5. Plug the returned clip into the **result** pipe.
6. Flip the node from **crop** mode to **uncrop** mode.

You now have the patch sitting back where it came from, at full plate
resolution, in an otherwise empty frame. Merge it over your plate however the
shot needs, through your roto or any other matte. The node does no compositing
itself, deliberately, so the edge treatment stays entirely yours.

## A full run through

Say you're removing a rig from a moving actor's shoulder on a 1920x1080 plate,
frames 1001 to 1120.

**Roto the rig.** Just enough to cover it. This matte is doing two jobs: telling
the node where to put the crop, and telling the model what to paint.

**Select the plate, then the roto, then make the node.** Everything is wired up.

**Set the range to 1001 to 1120 and hit Analyze roto.** The report tells you the
biggest your element ever gets, and how far it travels:

```
max bbox: 240 x 310 px     travel: 680 x 95 px
```

**Pick a resolution.** Your element is 240x310, so 512x512 would technically fit,
but the model would be looking at your rig filling the whole frame with no
surrounding context. 1024x1024 gives it room to understand what it's blending
into. Watch the report as you change it, and if you see

```
! bbox clipped on 12 of 120 frames
```

your element is bigger than the crop on those frames. Go up a size, or hit
**Set res to fit bbox** and it'll pick one for you.

**Check it in the viewer.** Mode is already on crop. You should see a 1024x1024
window with your rig sitting nicely in the middle, staying put as you scrub.

**Nudge it if you like.** If the rig is centred but you'd rather see more of the
shoulder below it, dial **offset** y down a bit.

**Write it out.** Hang a Write off the node, EXR, render.

**Now the mask.** Copy and paste the node, and plug the roto into the pasted
one's plate input. Same crop, same geometry, so you're looking at your matte
framed exactly like the picture. Write that out to a second path.

**Off it goes to ComfyUI.** Feed it the crop and the mask.

**When the patch comes back**, Read it in, plug it into the result input, flip
mode to uncrop. You're looking at a full 1920x1080 frame, empty except for your
patch sitting exactly where the crop came from.

**Comp it.** Merge it over your plate, through your roto or any other matte you
like. A Dilate and a small Blur on the matte before the Merge will hide the
join, which is a hard one pixel step otherwise. This is the part the node
deliberately leaves to you.

## The other controls

Most of the time you won't need these. When you do, here's what they're for.

**offset x / y**  
Shifts the crop window in pixels. Use it when the middle of your roto isn't
where you want the element sitting, say you need more headroom, or the subject
should sit off to one side. It's animatable, so you can drift the framing over a
shot if you need to. The crop won't leave the plate, so if you push it into a
frame edge it will hold there rather than pulling in black, and the report will
say `! offset limited by plate edge` so you know that's what happened.

**preset and resolution**  
Presets are the common model sizes. If you type your own, keep it to a multiple
of 32, because most models quietly pad or reject anything else. All the presets
already are.

**Set res to fit bbox**  
Picks the smallest valid size that fits your element on every frame. A starting
point rather than an answer, since it gives you no padding at all.

## A few things worth knowing

**You can put the node away and come back to it.** Render your crop, unplug
everything, open the script next month, flip to uncrop mode. Nothing needs
re-analyzing. All the numbers are saved on the node.

**Re-analyze after you change the roto.** Nothing watches your shapes, so if you
tweak them, hit Analyze again.

**The roto is only needed for Analyze.** It isn't wired into the picture at all,
so once you've analyzed you can unplug it and nothing changes. Leave it
connected anyway if you might want to re-analyze later.

**Keep the plate connected.** The crop is clamped to the plate's size, so the
node wants to know what it is. If you plug in a different plate it'll tell you
rather than quietly moving your crop.

## Install

Copy the `stabilized_crop` folder somewhere your Nuke picks up, such as your
studio share. It carries its own `init.py` and `menu.py`, so it only needs to be
on the plugin path. `stabilized_crop_v3.0.0.zip` in this repo has the folder and
this README together if you need to carry it somewhere. The zip is named for the
version inside it, so check it against the `tool_version` knob on a node before
assuming they match.

Restart Nuke and check whether it just worked, since some launchers add every
subfolder of the share automatically:

```python
import stabilized_crop
print(stabilized_crop.__file__)
```

If that errors, add one line to any `init.py` that already runs, or to your own
`~/.nuke/init.py` (`C:\Users\<you>\.nuke\init.py`, create it if it isn't there):

```python
nuke.pluginAddPath('S:/nuke/stabilized_crop')
```

Not sure which files already run? This will tell you:

```python
import os, nuke
print("NUKE_PATH:", os.environ.get("NUKE_PATH"))
print("HOME:     ", os.environ.get("HOME"))
for path in nuke.pluginPath():
    print("   ", path)
```

If `HOME` points at the share, then `~/.nuke` is a shared folder and one line
there covers everybody. Set `MENU_PATH` in `menu.py` to put the tool wherever
your menus live.

Two things: don't rename `stabilized_crop.py`, because the node's buttons look
for it by name and every saved node would break. And pasting the file into the
Script Editor isn't the same as installing it, the node will build and then
every button will fail. Render nodes need nothing installed, since the maths is
baked into the node itself.

## Under the hood

Skip this unless something looks wrong.

The bounding box is read straight off the roto control points, with no
rendering, and cached on the node. That's why changing resolution re-solves
instantly. The window is placed on the box centre each frame, clamped to stay on
the plate, and the first analyzed frame's window becomes a static Crop box that
every other frame is translated onto. Every move is a whole number of pixels
with an impulse filter, in both directions, which is what makes the round trip
exact.

```
in 0 plate ──── Stabilize ───── CropWindow ─────────────────┐
                (int trans)     (res_w x h)                 │
                                                            ├─ OutSwitch ─ out
in 1 roto  ──── (nothing: read by Analyze only)             │    (mode)
                                                            │
in 2 result ─── ResultPlace ─── Matchmove ─── PlateFrame ────┘
                (into stab)     (into plate)  (plate format)
```

Offline checks, no Nuke needed:

```
python3 tests/test_solve.py
```

### Versions

Nodes carry the version that built them, at the bottom of the panel.
`stabilized_crop.__version__` is what's installed. They can differ, which is
usually fine:

- **patch** fixes reach old nodes on their own. Just deploy.
- **minor** changes knobs or internals. Old nodes keep what they were built
  with, so rebuild one to pick it up.
- **major** changes what the node does, so a rebuilt node behaves differently
  from the one it replaces. Saved nodes carry on as they were.

### If something looks wrong

The crop side has plenty of mileage on it. The two newer bits are less proven,
and each is a one line fix:

| symptom | fix |
|---|---|
| uncrop comes out crop-sized | `PlateFrame`'s `reformat` or `box` is not taking |
| the roto input arrow is missing | the unconnected `Input` node in `_build_internals` needs a downstream stub |
| resolution stops updating live | `knobChanged` isn't firing; press **Analyze roto** |

Older nodes keep whatever they were built with, so shots in flight are safe.
Nodes built before v3.0.0 still carry a matte in alpha and have `alpha only` and
`use plate alpha`; before v2.0.0 they also comp internally and have `matte grow`
and `matte blur`. Rebuild a node to move it to the current behaviour.

## Files

```
stabilized_crop/            the folder you deploy
├── init.py
├── menu.py                 set MENU_PATH here
└── python/stabilized_crop.py

tests/test_solve.py         offline checks
reference/roto_to_bbox.py   the proof of concept this grew out of
```

## Credits

The world-space roto bbox maths comes from the `roto_to_bbox.py` proof of
concept, coded live with Claude and ChatGPT for
[this video](https://www.youtube.com/watch?v=lPamGg187Ac).

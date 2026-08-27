# KiCad → Makera Z1 drill & cutout g-code

A KiCad 10 action plugin that turns the open board into **one** Makera Z1
program: drill every through-hole, stop for a manual tool change, then cut the
outline to size.

It deliberately does **not** mill traces. This is built for a split workflow —
a 355nm UV galvo laser ablates the copper and the solder mask, and the mill only
does the work that goes all the way through the board. Each machine gets the job
where its weakness doesn't matter: the laser has no trouble holding depth on an
unlevel blank, and a through-cut doesn't care about ±0.05mm of Z error.

Copper artwork for the laser comes from its companion,
[kicad-lightburn-plugin](https://github.com/peterb154/kicad-lightburn-plugin).
Both reference the **drill/place (aux) origin**, and that shared datum is the
entire contract between them.

## What it does

1. **Collects holes** from the pcbnew API — through-hole pads, and vias only if
   you ask. Holes lying outside Edge.Cuts are treated as fixture holes and cut
   first, with an optional pause to fit dowel pins.
2. **Mills each hole helically** at `(hole − tool) / 2` radius, or plunges it
   when the bit is already the hole size.
3. **Offsets the outline** by the cutter radius and cuts it last, with holding
   tabs, because that's the op that frees the part.
4. **Verifies its own output** and refuses to write anything that would
   misbehave on the machine.

## No external tools

No Gerbers, no drill files, no pcb2gcode, no Inkscape. Offsetting is done with
`SHAPE_POLY_SET.Inflate` — Clipper, already inside KiCad. `Inflate` grows
*material*, which happens to be right for both cases at once: the board's outer
edge moves outward by the cutter radius, and an internal cutout's edge moves
inward by the same, putting the tool on the waste side of the line either way.

## Requirements

KiCad 10.x and its bundled Python. That's all.

## Install (development)

```sh
ln -s "$PWD/plugins" ~/Documents/KiCad/10.0/3rdparty/plugins/kicad-gcode-plugin
```

The link points at `plugins/`, not the repo root — KiCad loads that directory as
the Python package. `resources/` then sits a level above it, which is why
`_find_icon` looks for both that layout and the PCM one.

Then **Tools → External Plugins → Refresh Plugins**.

### Editing it: Refresh is not enough

**Restart KiCad after changing anything under `plugins/`.** *Refresh Plugins*
re-imports the top-level package, but the submodules it pulled in with
`from . import program` are still sitting in `sys.modules`, so your edit does
not take effect and KiCad keeps running the old code.

This fails in the worst way — silently, with a traceback whose line numbers
point at the *previous* version of the file, which sends you hunting for a bug
you already fixed. If a traceback's line number doesn't match what you see in
the file, that is the tell. Clearing bytecode is a good reflex too:

```sh
find . -name __pycache__ -type d -exec rm -rf {} +
```

## Running the output

**Use a sacrificial layer.** Through-cuts on this machine go ~0.2mm past the
far face, and the default depth is board thickness + 0.2mm.

1. Install the first bit → **Tool dropdown → Change tool → T<n>** so the
   controller registers it and sets its TLO.
2. Set XY origin to the drill/place origin. Don't move it again.
3. Run with **Auto Z Probe ON**. Z0 is tool-independent via TLO, so the
   in-program `M6` handles every later bit by itself.

## Machine facts encoded in `gcode.py`

These are measured on a real Z1, not assumed. They live in one module so the
toolpath code can stay about geometry:

- Spindle 0–13000 rpm. Every `S` is capped and the generator refuses more.
- Smoothieware **halts** on `G64`, `G81` and `G91.1`. Never emitted.
- `G28` is **"goto clearance/park"**, not a homing cycle. It belongs before `M2`.
- `M30` does nothing on a Carvera. Program end is `M2`.
- Any `T1`–`T99` makes `M6` prompt for a manual swap and re-zero on the setter.
  There is no carousel and **no tool-number floor** — an earlier note claimed
  `T1`–`T6` fail; that was wrong, and the real cause of "No tool or probe tool!"
  is the M495 restore bug.
- The **first** tool must be declared with an explicit `M6` at the top, or the
  auto-detect restore has nothing to resolve.
- Don't reuse a tool number for a different bit: if an `M6` requests the number
  already loaded, the controller may skip the setter re-zero.
- `ZSAFE` = 15mm clears the 7.2mm hold-down clamps; `ZCHANGE` = 20mm sits above
  it for the trip to the setter.

## Failing loudly

Wrong-looking g-code gets caught by a human; *plausible*-looking g-code doesn't.
So `verify.py` re-reads the generated text and refuses to write it if anything
is off — forbidden codes, overspeed, motion before the first `M6`, Z deeper than
the cut depth, an arc with no `I`/`J`, coordinates implying a units or origin
bug, or a program that doesn't end `M5 → G28 → M2`.

The generator refuses before that, too: a bit wider than the smallest hole, tabs
at or below the cut depth, tabs that don't fit around the perimeter, an outline
the cutter would collapse to nothing, and **oval/slotted drills** — a slot has to
be routed, and one round hole in the middle of it looks right in a preview and is
wrong on the board.

`Inflate` can also swallow an internal cutout smaller than the cutter. That's
reported rather than silently skipped, because "nothing was machined there" and
"there was nothing to machine" look identical in the output file.

## Notes from building this

- **Y sign is the whole ballgame.** KiCad's Y axis points down, the machine's
  points up. Every coordinate is negated in Y on the way out, in exactly one
  place (`geometry.to_machine`). Miss it and a symmetric outline comes out
  looking perfect with all the holes mirrored.
- **Full circles are ambiguous.** A `G2` whose start and end coincide is read as
  a full turn by some controllers and as no motion at all by others. Every
  revolution here is emitted as two half-circles.
- **Rapid the air.** pcb2gcode feeds the whole descent from `zsafe` at the
  plunge rate — with `zsafe` at 15mm that's a 15mm crawl on *every* hole. Only
  the last millimetre, the part that can touch material, is fed.
- **Vias are off by default and that's deliberate.** On a ViaGrid blank the vias
  are already in the blank; drilling them destroys the feature you bought it for.

## The icon

`resources/icon.svg` is the source; `icon.png` (26px, toolbar) and `icon_64.png`
(PCM listing) are rendered from it with `rsvg-convert`.

It was drawn for 24px rather than shrunk down from something larger, which is a
real constraint and not a stylistic one: strokes thinner than a pixel dissolve,
so the icon carries no outlines at all and gets its contrast from two shank
tones instead. That also lets it survive on a light and a dark toolbar without a
second asset. The first attempt ignored this and read as a thermometer.

The bit is drawn pointed even though the real ones are flat-bottomed
corn/fishtail cutters -- a flat tip at 24px renders as a blob. Regenerate with:

```sh
rsvg-convert -w 26 -h 26 resources/icon.svg -o resources/icon.png
rsvg-convert -w 64 -h 64 resources/icon.svg -o resources/icon_64.png
```

## Licence

MIT

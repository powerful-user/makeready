# makeready

Describe a print layout and get a real InDesign file back. Write the description yourself, or hand it to your agent.

It covers the measuring, framing, placing and checking that comes before someone opens InDesign to finish the job.

A layout in makeready is a few small text files: a template that says where the frames go, a spec that fills them with copy and photos, and brand files for swatches and fonts. `makeready build` compiles them into native InDesign documents, with real frames, links, bleed, swatches and fonts, ready to open, package and send.

## Two ways to use it

### With an agent

Give your agent the brief the way you would brief a production artist:

> Three instances of this window group, 5 ft apart. The six panes share one image frame, so one image spans all of them. Make two variants with an 8 ft door between windows one and two, and between two and three.

The agent writes the same template and spec files, runs the same commands and shows you the proofs. You answer in plain language ("make the bar 3 in", "swap the photo", "add a door") and it edits and builds again.

makeready is made to be driven this way. Every command runs without prompts and reports in plain text, the inputs are files an agent can read and diff, the same inputs always give the same build, and the pre-print checks catch mistakes before anyone opens InDesign. When a job needs something the compiler doesn't write yet, the agent can add it in a `hooks.py` beside the template, which the build runs for every frame it writes.

The `makeready` agent skill in `skills/makeready/` teaches an agent this workflow: ask about missing measurements, preview before building, read the build back and check it against the brief. Install it with `npx skills add powerful-user/makeready`.

### By hand

The files are plain YAML and JSON, so any text editor works. A series of flyers, for example, is one template and a short spec:

```yaml
# series/spring/series.yaml
template: flyer
defaults:
  slots:
    body: copy/body.md
    photo:hero: {src: kitchen.jpg, fit: fill, focus: [0.5, 0.35]}
variants:
  - doc: open-house
    slots:
      headline: Open House, April 12
  - doc: garden
    slots:
      headline: Garden Party
    overrides:
      headline: {size: 54}
```

```
makeready build spring            # every flyer in the series
makeready render spring/garden    # print PDF from InDesign on the Mac
makeready proof spring/garden     # page images to check
```

Change a headline, swap a photo or nudge the tracking in the spec, then build again. If the design starts in Figma, `makeready import-figma` turns its frames into templates, so you only write the spec.

## What it does

- **Compiles layouts to IDML on any machine.** Pages of any size, image frames, shapes, text with paragraph and character styles, CMYK swatches and placed PDF or AI artwork, all written as InDesign's own markup. No InDesign is needed to build.
- **Draws any frame shape.** A frame can be a polygon, or one compound frame made of several outlines that share a single image. Images can be placed to the exact point, so one photo lines up across separate frames or pages, and guide lines can be marked non-printing.
- **Imports from Figma.** Frames become templates: geometry, rotation, images, text and swatches.
- **Renders on a Mac.** `makeready render` sends a build to desktop InDesign over SSH, which opens it, exports a print PDF and reports back.
- **Checks before print.** Every build reports missing files, low-resolution images, art that stops short of the bleed, colours outside the brand swatches, inks the printer doesn't run, and fonts that are unmapped or missing.
- **Hands over clean.** `makeready approve` packages the signed-off document in InDesign, fonts and links included, and makeready stops touching it.

## Example: a window wall

The agent brief above produced one file per variant at 1:4 scale. Each page holds a single compound image frame made of six panes: 101 × 42.5 in over 101 × 93 in, a 4 in bar between them, 0.5 in bleed per pane. One image sits in that frame, so the scene runs across every window and behind the walls and the door. Non-printing outlines mark the glass edges and the door opening. A rainbow grid placeholder labels each pane, so a misplaced one is easy to spot, and the real artwork drops in by relinking.

Before anything went into InDesign, the agent drew the three layouts as a flat preview to confirm the measurements. After the build, it read each file back and checked every pane and gap against the brief. The whole layout is plain template data: one frame with six outlines, an exact image placement, and non-printing guides.

## How it works

```
Figma file ─▶ makeready import-figma ─▶ templates/<name>/
templates + brand/ + photos/ + series/<series>/ ─▶ makeready build ─▶ IDML + links + fonts
build ─(rsync + ssh)─▶ InDesign on the Mac ─▶ .indd, print PDF, report
PDF ─▶ makeready proof ─▶ page images ─▶ review ─▶ edit the spec ─▶ build again
makeready approve ─▶ packaged InDesign document
```

A template is a list of pages and elements: frames with positions, sizes, assets and styles. A spec picks a template and fills its slots with copy and photos, with per-document overrides. `brand/` holds swatches and a font map.

## Commands

A target is `series/doc`, or a whole series for `build` and `render`.

| Command | What it does |
|---|---|
| `makeready import-figma <file-key> --name <template>` | Read a Figma file into a template; a re-import keeps keys set by hand, such as `hooks` and `bleed` |
| `makeready build <target>` | Compile to IDML with links and fonts |
| `makeready render <target>` | Open the build in InDesign on the Mac and export a print PDF |
| `makeready proof <series/doc>` | Turn the PDF into page images for review |
| `makeready open <series/doc>` | Open the rendered document on the Mac |
| `makeready approve <series/doc>` | Package the signed-off document in InDesign |
| `makeready mac check` / `mac bootstrap` | Check the Mac and set up its scripts and reference file |
| `makeready mac-reset` | Clear a stuck render |

## Setup

Needs Python 3.12 and [uv](https://docs.astral.sh/uv/). Rendering needs a Mac with desktop InDesign, reachable over SSH with Remote Login on.

```
uv sync
uv run makeready --version
```

Configuration lives in `~/.config/makeready/config.toml`, outside git: the Figma token, the Mac's host and home folder, where photos and fonts live, and the print profile and inks. On the Mac, jobs, fonts and scripts live under `~/makeready/`.

## Status

Early. The build, the checks and the Mac render work and have been used on live jobs; the full run from a Figma file to an approved package is still being finished. The build and checks run anywhere; rendering and packaging need the Mac.

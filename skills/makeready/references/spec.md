# Project, spec and commands

## Project layout

```
<project>/
  seed/reference.idml       InDesign reference file the build copies from (from the makeready repo)
  mac/                      scripts the Mac render runs (from the makeready repo)
  brand/swatches.yaml       CMYK swatches, the only colours a build may use
  brand/font-map.yaml       font names to font files in the fonts folder
  brand/asset-map.yaml      Figma element ids to vector files (import only)
  templates/<name>/         template.json, optional hooks.py and template-overrides.yaml
  series/<series>/          series.yaml and one .yaml per document, plus copy files
  build/<series>/<doc>/     builds; latest/ points at the newest
```

The config (`~/.config/makeready/config.toml`, or the file `MAKEREADY_CONFIG` names) says where the photos and fonts folders are, which Mac renders, and the print profile and inks. Do not print or copy the Figma token it holds.

`brand/swatches.yaml`:

```yaml
swatches:
  - {name: Brand Blue, hex: "#0055A5", cmyk: [100, 60, 0, 5], pantone: PANTONE 2935 C, spot: true}
  - {name: Guide, hex: "#000000", cmyk: [0, 0, 0, 100]}
```

`brand/font-map.yaml`:

```yaml
fonts:
  Nimbus Sans/Regular: {family: Nimbus Sans, style: Regular, file: NimbusSans-Regular.otf}
```

## Series and documents

A document is `<series>/<doc>`. Names are lower case letters, digits and hyphens. A series file can describe every document:

```yaml
# series/spring/series.yaml
template: flyer
defaults:
  slots:
    body: copy/body.md
    photo:hero: {src: kitchen.jpg, fit: fill, focus: [0.5, 0.35]}
  overrides:
    Band: {opacity: 0.9}
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

A document can also have its own file, `series/spring/garden.yaml`, with `template`, `slots`, `overrides` and `status`. Later layers win: `templates/<name>/template-overrides.yaml`, then the series `defaults`, then the variant, then the document file. A document with no series file needs its own `template`.

### Slots

Keys are the `slot` names in the template.

- Text slot: the text itself, or a `.md` or `.txt` file in the series folder (`copy/body.md`). In a file, a blank line starts a new paragraph, a line break inside a paragraph stays a line break, each `- ` line is a bullet paragraph, and `**bold**`, `*italic*` and `***both***` use the font's bold and italic styles.
- Image slot: `{src, fit, focus}`. `src` is a file in the photos folder. `fit` is `fill` (cover the frame, the default), `fit` (show the whole image) or `none`. `focus` is two numbers from 0 to 1 that say which point of the photo stays in view when it is cropped.

### Overrides

Keys are element ids; values change that element for this document only.

| Key | Meaning |
|---|---|
| `x`, `y` | move by this many points (added to the template position) |
| `width`, `height` | new size in points |
| `size`, `tracking`, `leading` | text size (pt), tracking (thousandths of an em), leading (pt) |
| `swatch` | new fill swatch |
| `opacity` | 0 to 1 |
| `hidden` | `true` leaves the element out |

### Status

`status: draft` (the default) or `approved`. `makeready approve` sets `approved` after packaging; a build of an approved document needs `--reopen`, which discards hand edits made in the packaged `.indd`.

## Commands

All take `--repo <project>` before the command.

| Command | What it does |
|---|---|
| `makeready build <series>/<doc>` or `build <series>` | Check and compile to `build/<series>/<doc>/<id>/<doc>.idml`, with `links/`, `Document Fonts/`, `checks.json` and `geometry.json` |
| `makeready mac check` | Can the Mac be reached and does it have what a render needs |
| `makeready render <series>/<doc>` | Open the build in InDesign on the Mac and export a print PDF |
| `makeready proof <series>/<doc>` | Page images from the PDF |
| `makeready open <series>/<doc>` | Open the rendered document on the Mac |
| `makeready approve <series>/<doc>` | Package the signed-off document |
| `makeready import-figma <key> --name <template>` | Read a Figma file into a template |

## Findings

A build prints its findings and writes the warnings to `checks.json`. Errors stop the build: a missing file, an unknown swatch, an unmapped or missing font, inks the printer does not run. Warnings do not: art that stops short of the bleed, or an image under 300 ppi at document size. At 1:4, document ppi is four times full-size ppi; the read-back's `--scale` reports full-size ppi so you can judge it against what the printer needs.

## Builds are cached

The build id is a hash of every input file: template, hooks, template overrides, spec and copy files, brand files, assets, photos, fonts, the seed and the Mac scripts. Building the same inputs again reuses the same folder. makeready's own code is not an input, so a change to it does not rebuild a document; say so rather than expecting a new build.

# Template format

A template is `templates/<name>/template.json` in the project. `makeready import-figma` writes one from a Figma file; you can also write it by hand or from a generator script. All lengths are points (72 per inch) at document size, so at 1:4 one full-size inch is 18 pt.

## Top level

```json
{
  "name": "window-wall",
  "scale": 0.25,
  "bleed": 9,
  "hooks": "hooks.py",
  "pages": [ ... ]
}
```

- `name`: the template's folder name.
- `scale`: a note of the scale the template is drawn at. makeready does not resize anything from it; it is there so readers know.
- `bleed`: bleed on every side of every page, in points. 0.5 in full size is 36 pt, or 9 pt at 1:4.
- `hooks`: optional. The name of a `.py` file beside `template.json`. See Hooks below.
- `figma`: written by the importer; leave it alone.
- `pages`: a list of pages, in order.

A re-import keeps every top-level key the import does not write itself, so `hooks` and a hand-set `bleed` survive it. `pages` are replaced.

## Pages

```json
{"name": "Base - three windows", "width": 7614, "height": 2511, "elements": [ ... ]}
```

`width` and `height` are points; neither may pass 15552 pt (216 in). `name` is for people; it does not reach the IDML. Elements are drawn in list order, first at the back.

## Elements

Every element has:

- `id`: unique on the page. It is how the spec, the checks and the read-back name the frame. A text element's `id` becomes its paragraph style name, so never put a colon in it.
- `kind`: `text`, `image`, `shape` or `vector`.
- `transform`: six numbers `[a, b, c, d, tx, ty]` mapping the element's own points to the page, with the origin at the page's top-left corner and y down. `[1, 0, 0, 1, 36, 48]` puts the element's top-left corner 36 pt right and 48 pt down. A rotation by angle t is `[cos t, sin t, -sin t, cos t, tx, ty]`.
- `width`, `height`: the frame's size in its own points, before the transform.
- `opacity`: optional, 0 to 1, default 1.
- `fill`: `{"swatch": "Brand Blue", "opacity": 1}` or `null`. Swatch names come from `brand/swatches.yaml`.
- `stroke`: `{"swatch": "Guide", "weight": 0.5, "opacity": 1}` or `null`.
- `slot`: optional. A name the spec fills (`headline`, `photo:hero`).

### text

```json
"text": {"characters": "Headline", "font": "Nimbus Sans/Bold", "size": 48, "tracking": -20,
         "leading": 57.6, "align": "left", "valign": "top", "auto_height": true}
```

`font` is `Family/Style` and must be a key in `brand/font-map.yaml`. `tracking` is in thousandths of an em. `align` is left, center, right or justify; `valign` is top, center or bottom. `characters` is used when the spec leaves the slot empty. The fill swatch is the text colour.

### image

The picture comes from the spec when the element has a `slot` (a file in the photos folder from the config), otherwise from `asset`: a path inside the project such as `images/placeholder.jpg`. JPEG, PNG, TIFF, GIF, WebP and BMP work.

- `paths`: optional. A list of closed outlines, each three or more `[x, y]` points in the element's own points. One outline makes a polygon frame; several make one compound frame, so one image shows through every outline. Grow each outline by the bleed where it meets a trimmed edge.
- `place`: optional. Six numbers mapping image pixels to the frame's own points, which fixes the image exactly: `[k, 0, 0, k, dx, dy]` scales it by k and moves its top-left corner to (dx, dy). Without `place` the image fills the frame, centred, or as the spec's `fit` and `focus` say, so it moves and rescales whenever the frame's size changes. Use `place` whenever one image must sit the same way on several pages or documents, such as a base layout and its variants: work out one scale and offset from the full-size wall and give every page the same image scale, so the scene stays put when a door or a gap changes the frame.

### shape

`shape` is `rect` (default) or `oval`. `paths` works as for images. `nonprinting: true` marks a guide: it shows in InDesign, does not print, and the bleed check skips it. Use non-printing shapes with a stroke and no fill to mark glass edges, door openings or fold lines.

### vector

`asset` is a PDF or AI file inside the project, placed to fill the frame.

## Hooks

When a job needs something the compiler does not write, put it in a hook instead of changing makeready:

```python
# templates/flyer/hooks.py
def item(element, frame, context):
    if context.doc != "garden":
        return None
    frame.set("Name", "garden " + element["id"])  # the frame's name in the Layers panel
    return None
```

A frame's IDML keeps makeready's own `aid.id` label in `Properties/Label`; render and package find frames by it, so add to that element rather than replacing it.

The build calls `item` once for every frame it writes. `element` is the template element (a dict). `frame` is the frame's IDML XML (`xml.etree.ElementTree.Element`), ready to change in place. `context` has `new_id()` for a fresh item id when you add frames, and `series`, `doc` and `page` (the template page name) so a change can be limited to one document or page. Return `None` to keep the frame, an element to replace it, or a list of elements to put several in its place. The hook file is a build input: changing it gives a new build.

## Example: one image across six panes

A 1:4 page holding three windows, each a pane B over pane A with a bar between, as one compound image frame:

```json
{"id": "window-art", "kind": "image", "asset": "images/scene.jpg",
 "transform": [1, 0, 0, 1, -9, -9], "width": 7632, "height": 2529,
 "paths": [[[0, 0], [0, 783], [1836, 783], [1836, 0]], ...],
 "place": [1.2, 0, 0, 1.2, 0, -40]}
```

Generate numbers like these from a script that holds the full-size measurements, rather than typing them.

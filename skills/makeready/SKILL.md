---
name: makeready
description: Use when asked to make, change, rebuild or check a print layout (an InDesign file, a flyer series, a poster, a window or wall graphic) with the makeready CLI, or when working in a project folder that holds templates/ and series/ for makeready
---

# makeready

## Overview

makeready compiles layout files into native InDesign IDML. A template places the frames, a spec fills them, and `makeready build` writes the document and checks it. Your job is to turn a brief into those files and prove the result matches the brief, from the files themselves.

File formats: `references/template.md` (templates, frames, shapes, hooks) and `references/spec.md` (series, slots, overrides, commands, project layout).

## Steps

1. **Find the project.** It is the folder holding `seed/`, `mac/`, `brand/`, `templates/` and `series/`. Run every command as `makeready --repo <project> <command>`. Write only inside the project.

2. **Turn the brief into numbers.** List every size, gap, count and margin the layout needs. Name each one the brief leaves out, such as the bar between two panes or a page margin. Ask about each. If nobody can answer, choose a value, say it is an assumption, and keep it in one named place so changing it is one edit.

3. **Choose scale and bleed, and say which.** An InDesign page is at most 216 in on each side. When the job is larger, build to scale: 1:4 is 18 pt per full-size inch, 1:10 is 7.2 pt. Bleed is 0.5 in at full size unless the printer says otherwise; the template's `bleed` is in document points (9 pt at 1:4).

4. **Preview a new or changed layout before building.** Draw each page flat, as a PNG or SVG made from the same numbers, with the key dimensions labelled. Show it and wait for a yes. If nobody can look, say the preview was not confirmed and carry on.

5. **Edit the right file.** Copy, photos, sizes and nudges for one document go in the spec (`slots`, `overrides`), never in `template.json`. Layout goes in the template. For repeated geometry, write a small generator script inside the project that holds the numbers from step 2 and writes `template.json`.

6. **Extend with a hook, not a patch.** When the compiler does not write something, add `hooks.py` beside `template.json`, name it in the template's `hooks`, and define `item(element, frame, context)`. Use `context.doc` and `context.series` so a change meant for one document reaches only that one.

7. **Build and report the findings.** `makeready --repo <project> build <series>/<doc>`. Read every finding it prints and `checks.json` beside the IDML. Report each one as it is; do not drop a warning or call it harmless without saying why. Builds are cached by their inputs: a change to makeready's own code does not rebuild a document.

8. **Read the build back.** `python3 <skill folder>/scripts/readback.py <project>/build/<series>/<doc>/latest/<doc>.idml --scale <scale>` lists every page, frame, outline and placed image in full-size inches. Check each size, position and gap against the numbers from step 2, and say which match and which do not.

9. **Render only with a Mac.** Run `makeready mac check` first. If it fails, do not render; say that no PDF was made and why. `makeready render`, `proof` and `approve` need the Mac.

10. **Report.** Files changed, commands run, assumptions made, findings, the read-back result, and what is left undone.

## Never

- Edit, patch or monkeypatch makeready's source or the installed package.
- Write files outside the project, even a backup or a scratch file.
- Put a copy or size change for one document in `template.json`.
- Make a page larger than 216 in on a side.
- Put a colon in a text element's `id`: it becomes a paragraph style name, and InDesign 2026 crashes opening a style name with a colon.
- Render, or contact the Mac, when `makeready mac check` fails; quit InDesign or close a document makeready did not open.
- Rebuild an approved document with `--reopen` unless the user asked: it discards hand edits to the packaged file.
- Call the job done without the read-back in step 8.

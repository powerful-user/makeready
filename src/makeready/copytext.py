"""A small markdown subset: paragraphs, bullets, and bold and italic runs."""

from dataclasses import dataclass

# U+2028 is the forced line break InDesign keeps inside one paragraph.
_LINE_BREAK = "\u2028"
_MARKERS = ((3, "BoldItalic"), (2, "Bold"), (1, "Italic"))


@dataclass
class Run:
    text: str
    style: str | None


@dataclass
class Para:
    runs: list[Run]
    bullet: bool


def parse(text: str) -> list[Para]:
    paragraphs: list[Para] = []
    block: list[str] = []

    def flush() -> None:
        nonlocal block
        if not block:
            return
        plain: list[str] = []

        def flush_plain() -> None:
            nonlocal plain
            if not plain:
                return
            paragraphs.append(Para(_runs(_LINE_BREAK.join(plain)), False))
            plain = []

        for line in block:
            if line.startswith("- "):
                flush_plain()
                paragraphs.append(Para(_runs(line[2:]), True))
            else:
                plain.append(line)
        flush_plain()
        block = []

    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped:
            block.append(stripped)
        else:
            flush()
    flush()
    return paragraphs


def _runs(text: str) -> list[Run]:
    runs: list[Run] = []
    start = 0
    index = 0
    while index < len(text):
        matched = _marker(text, index)
        if matched is None:
            index += 1
            continue
        style, content, end = matched
        if start < index:
            runs.append(Run(text[start:index], None))
        runs.append(Run(content, style))
        index = end
        start = end
    if start < len(text):
        runs.append(Run(text[start:], None))
    return _merge(runs)


def _marker(text: str, index: int) -> tuple[str, str, int] | None:
    for length, style in _MARKERS:
        marker = "*" * length
        if not text.startswith(marker, index):
            continue
        content_at = index + length
        close = text.find(marker, content_at)
        if close < 0:
            continue
        content = text[content_at:close]
        if not content or content[0].isspace() or content[-1].isspace():
            continue
        return style, content, close + length
    return None


def _merge(runs: list[Run]) -> list[Run]:
    merged: list[Run] = []
    for run in runs:
        if not run.text:
            continue
        if merged and merged[-1].style == run.style:
            merged[-1] = Run(merged[-1].text + run.text, run.style)
        else:
            merged.append(run)
    return merged

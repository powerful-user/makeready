"""The agent skill in skills/makeready: frontmatter, length and the files it names."""

import re

from fixture_repo import ROOT

SKILL = ROOT / "skills" / "makeready"


def text():
    return (SKILL / "SKILL.md").read_text()


def test_frontmatter_names_the_skill_and_its_trigger():
    match = re.match(r"---\nname: (.+)\ndescription: (.+)\n---\n", text())
    assert match, "SKILL.md must start with name and description frontmatter"
    assert match.group(1) == "makeready"
    assert match.group(2).startswith("Use when ")


def test_skill_is_under_700_words():
    assert len(text().split()) < 700


def test_every_file_the_skill_names_exists():
    named = set(re.findall(r"((?:references|scripts)/[\w-]+\.\w+)", text()))
    assert {"references/template.md", "references/spec.md", "scripts/readback.py"} <= named
    for path in named:
        assert (SKILL / path).is_file(), path

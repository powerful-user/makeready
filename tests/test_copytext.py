from makeready.copytext import Para, Run, parse


def test_plain():
    assert parse("Hello world") == [Para([Run("Hello world", None)], False)]


def test_paragraphs_and_line_breaks():
    assert parse("One\ntwo\n\nThree") == [
        Para([Run("One\u2028two", None)], False),
        Para([Run("Three", None)], False),
    ]


def test_inline_styles():
    assert parse("Join us **Saturday** for *coffee* and ***cake***.") == [
        Para(
            [
                Run("Join us ", None), Run("Saturday", "Bold"), Run(" for ", None), Run("coffee", "Italic"),
                Run(" and ", None), Run("cake", "BoldItalic"), Run(".", None),
            ],
            False,
        )
    ]


def test_bullets():
    assert parse("Intro\n- Tours every hour\n- Free **parking**") == [
        Para([Run("Intro", None)], False),
        Para([Run("Tours every hour", None)], True),
        Para([Run("Free ", None), Run("parking", "Bold")], True),
    ]


def test_unclosed_marker_is_literal():
    assert parse("2 * 3 = 6") == [Para([Run("2 * 3 = 6", None)], False)]


def test_trailing_whitespace_and_blank_lines():
    assert parse("\n\nA  \n\n\nB\n") == [Para([Run("A", None)], False), Para([Run("B", None)], False)]

"""Search-text parsing (pure functions, no database): what becomes a prefix, what stays websearch syntax."""
import re

import pytest

from services.catalog.query import fuzzy_text, parse_search, prefix_tsquery


@pytest.mark.parametrize("text, rest, prefix", [
    ("hood", "", "hood"),
    ("  Hood  ", "", "hood"),
    ("blue hood", "blue", "hood"),
    ("hoodie -red", "hoodie -red", None),        # an exclusion must stay an exclusion
    ('"red hoodie"', '"red hoodie"', None),       # a quoted phrase is exact
    ("hoodie or", "hoodie or", None),             # `or` is an operator, not a word to complete
    ("", "", None),
    ("   ", "", None),
    ("running-shoe", "", "running-shoe"),
])
def test_last_word_becomes_the_prefix_unless_it_is_syntax(text, rest, prefix):
    terms = parse_search(text)
    assert (terms.rest, terms.prefix) == (rest, prefix)


def test_prefix_is_sanitised_before_it_reaches_a_tsquery():
    for hostile in ["hood'; DROP TABLE products; --x", "a:*|b", "x) | (y", "h@ck3r<->"]:
        terms = parse_search(hostile)
        if terms.prefix is not None:
            assert re.fullmatch(r"[a-z0-9-]+", terms.prefix)
            assert prefix_tsquery(terms.prefix) == f"'{terms.prefix}':*"


def test_fuzzy_text_keeps_only_plain_words():
    assert fuzzy_text("  Hodie!! ") == "hodie"
    assert fuzzy_text("blue 'hoodie'; drop") == "blue hoodie drop"
    assert fuzzy_text("???") == ""

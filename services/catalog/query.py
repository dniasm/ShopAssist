"""Turn what a user typed into search terms. Pure functions, no database.

Why: people type half a word ("hood") or misspell it ("hodie"). The last word is searched as a PREFIX, the
rest keeps websearch syntax (quotes, `-exclude`, `or`), and a trigram fallback handles typos. The prefix is
the only text we put into a tsquery string, so it is reduced to [a-z0-9-] first; everything else travels as a
bound parameter.
"""
import re
from dataclasses import dataclass

_LAST_WORD = re.compile(r"([A-Za-z0-9][A-Za-z0-9-]*)$")


@dataclass(frozen=True)
class SearchTerms:
    rest: str              # websearch syntax, everything before the last word (or the whole text)
    prefix: str | None     # last word, lower-case [a-z0-9-], searched as a prefix


def parse_search(text: str) -> SearchTerms:
    """Split off the last word as a prefix, unless it is syntax (`-word`, `or`) or inside an open quote."""
    text = text.strip()
    match = _LAST_WORD.search(text)
    if not match:
        return SearchTerms(text, None)  # ends in a quote or symbol: the user finished the phrase
    word, rest = match.group(1).lower().rstrip("-"), text[: match.start()].rstrip()
    if not word or word == "or" or rest.endswith("-") or text[: match.start()].count('"') % 2 == 1:
        return SearchTerms(text, None)
    return SearchTerms(rest, word)


def prefix_tsquery(prefix: str) -> str:
    """A tsquery literal for a sanitised prefix: `hood` -> `'hood':*` (the word is quoted, so no operators leak in)."""
    return f"'{prefix}':*"


def fuzzy_text(text: str) -> str:
    """Plain lower-case words only, for trigram matching."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))

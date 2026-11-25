"""Rule-based product tags from the seeded description pattern (no I/O)."""
import re

_STOP = frozenset("""this that these those with from made built crafted features feature keep keeps have has and the for are
is it its they them their offer offers offered suit suits designed work works comes come into over than also very
reliable performance construction colours colour wardrobes most just well easy care last stays dry wet weather""".split())
_WORD = r"[a-z0-9][a-z0-9-]*"
_MATERIAL = re.compile(rf"(?:made from|built from|made with|crafted with|made of)\s+(?:an?\s+|\d+\w*\s+)?({_WORD})")
_SUBJECT = re.compile(rf"(?:this|these)\s+({_WORD})(?:\s+{_WORD})?\s+(?:is|are|features?|keeps?|has|have|offers?|suits?|work)")
_FEATURE = re.compile(rf"(?:features?|keeps?|has|have)\s+(?:an?\s+|the\s+)?({_WORD})")
_USE = re.compile(rf"(?:for|suit|suits)\s+(?:an?\s+|the\s+)?({_WORD})")


def tags(description: str) -> list[str]:
    """3-5 lower-case tags: material, subject modifier, feature, use; padded with the longest content words."""
    text = (description or "").lower()
    found: list[str] = []
    for pattern in (_MATERIAL, _SUBJECT, _FEATURE, _USE):
        m = pattern.search(text)
        if m and m.group(1) not in _STOP and m.group(1) not in found:
            found.append(m.group(1))
    if len(found) < 3:
        words = sorted({w for w in re.findall(_WORD, text) if w not in _STOP and len(w) > 2 and w not in found},
                       key=lambda w: (-len(w), w))
        found += words[:4 - len(found)]
    return found[:5]

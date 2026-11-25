"""Fit-based size suggestions, deterministic and free of I/O: which sizes to search for a person with a base size and a fit.

`facts` here are the person's saved facts as dicts with key, value, category and kind (permanent | temporal).
"""
import re

LETTERS = ["XS", "S", "M", "L", "XL", "XXL"]
SIZE_KEY = {"t-shirts": "size_top", "hoodies": "size_top", "jackets": "size_top", "jeans": "size_bottom", "shoes": "shoe_size"}
JEANS_STEP = 2
_TIGHT = re.compile(r"\b(tight|slim|fitted)\b", re.I)
_LOOSE = re.compile(r"\b(loose|oversized|relaxed|baggy)\b", re.I)


def fit_in(text: str) -> str:
    """'tight' or 'loose' when the text says so (tight wins a tie, it is checked first); '' otherwise."""
    if _TIGHT.search(text):
        return "tight"
    return "loose" if _LOOSE.search(text) else ""


MAX_STEPS = 3


def suggest_sizes(base: str, fit: str, category: str, available: list[str] | None = None, steps: int = 1) -> list[str]:
    """The base size first, then `steps` sizes (1-3) in the direction the fit asks for (tight smaller, loose bigger);
    only sizes in `available` when it is given. Shoes and an unknown base give just [base] or []."""
    base = base.strip().upper()
    if not base:
        return []
    sizes = [base]
    direction = {"tight": -1, "loose": 1}.get(fit, 0)
    steps = max(1, min(MAX_STEPS, int(steps or 1)))
    if direction and category != "shoes":
        for n in range(1, steps + 1):
            if base in LETTERS:
                i = LETTERS.index(base) + direction * n
                if 0 <= i < len(LETTERS):
                    sizes.append(LETTERS[i])
            elif category == "jeans" and base.isdigit() and int(base) + direction * n * JEANS_STEP > 0:
                sizes.append(str(int(base) + direction * n * JEANS_STEP))
    if available:
        have = {a.strip().upper() for a in available}
        sizes = [s for s in sizes if s in have]
    return sizes


def base_size(facts: list[dict], category: str) -> str:
    """The saved size for this category (size_top for tops, size_bottom for jeans, shoe_size for shoes); '' when none."""
    key = SIZE_KEY.get(category)
    values = [f for f in facts if f["key"] == key]
    values.sort(key=lambda f: f.get("category", "") != category)  # a size saved for this very category first
    return values[0]["value"] if values else ""


def resolve_fit(message: str, facts: list[dict], category: str) -> str:
    """The fit for this request: the message's own words, then an open temporal want/need for the category that names a fit,
    then the saved fit for the category, then the saved fit for all categories. '' when nothing says anything."""
    if said := fit_in(message):
        return said
    for f in facts:
        if f["kind"] == "temporal" and f["key"] in ("want", "need") and f.get("category", "") in (category, "") and (said := fit_in(f["value"])):
            return said
    saved = {f.get("category", ""): f["value"].strip().lower() for f in facts if f["key"] == "fit" and f["kind"] == "permanent"}
    return saved.get(category) or saved.get("") or ""

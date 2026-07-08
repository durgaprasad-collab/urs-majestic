"""Maps a raw item-line string from a channel (Zomato/Swiggy) order export to
a canonical menu_items row.

Matching order:
  1. Combo suffix code — a raw name ending in a bare "(NN)" (e.g. Swiggy's
     "Mini North Indian Meal (01)") maps directly to the menu item named
     "Combo NN - ...", regardless of the descriptive text before it.
  2. Explicit alias — normalized raw name is a known alias (spelling/word-order
     mismatches the channels use, e.g. "Mushroom Biryani" -> "Mushroom Briyani
     / Pulav", or the compound "<X> Briyani, Raita , <Y> 65" combo strings).
  3. Generic normalized match — lowercase, strip bracketed qualifiers
     ("[10 pieces]", "(1 No)", "(01)") and punctuation, then exact match
     against menu_items.name normalized the same way. If more than one menu
     item collapses to the same normalized key, that key is treated as
     ambiguous and generic matching refuses to guess.
  4. No match -> None (caller stores raw_name with menu_item_id NULL).
"""
import re

from sqlalchemy.orm import Session
from app.models.menu_item import MenuItem

_BRACKET_RE = re.compile(r"[\[\(][^\]\)]*[\]\)]")
_PUNCT_RE = re.compile(r"[^\w\s]")
_COMBO_SUFFIX_RE = re.compile(r"\((0?[1-9])\)\s*$")
_COMBO_NAME_RE = re.compile(r"^Combo (\d{2})")


def normalize(name: str) -> str:
    s = name.lower()
    s = _BRACKET_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Normalized-raw-name -> exact menu_items.name. Seeded per spec, plus one
# disambiguation (Mushroom Manchurian) found necessary against real exports:
# both "(Gravy)" and "(Starter)" variants exist and normalize identically,
# so generic matching treats that key as ambiguous — this alias resolves it
# to the variant actually used in Zomato's export.
ALIASES: dict[str, str] = {
    normalize("Gobi Chilli"): "Chilli Gobi",
    normalize("Mushroom Chilli"): "Chilli Mushroom",
    normalize("Veg Biryani"): "Veg Briyani / Pulav",
    normalize("Mushroom Biryani"): "Mushroom Briyani / Pulav",
    normalize("Paneer Biryani"): "Paneer Briyani / Pulav",
    normalize("Paneer Tikka"): "Paneer Tikka (5 Pcs)",
    normalize("Mushroom Manchurian"): "Mushroom Manchurian (Gravy)",
    normalize("Veg Briyani, Raita , Gobi 65 (5 P Cs)"): "Combo 06a - Briyani Express (Veg)",
    normalize("Mushroom Briyani, Raita , Mushroom 65 (5 P Cs)"): "Combo 06b - Briyani Express (Mushroom)",
    normalize("Paneer Briyani, Raita , Paneer 65 (5 P Cs)"): "Combo 06c - Briyani Express (Paneer)",
}


class MenuIndex:
    """Normalized-name lookup over menu_items, built once per upload."""

    def __init__(self, db: Session):
        rows = db.query(MenuItem.id, MenuItem.name).all()
        self._by_name: dict[str, int] = {name: mid for mid, name in rows}
        self._combo_by_num: dict[str, int] = {}
        normalized: dict[str, int | None] = {}
        for mid, name in rows:
            key = normalize(name)
            if key in normalized and normalized[key] != mid:
                normalized[key] = None  # ambiguous: two items share this key
            else:
                normalized[key] = mid
            m = _COMBO_NAME_RE.match(name)
            if m:
                self._combo_by_num[m.group(1)] = mid
        self._normalized = normalized

    def match(self, raw_name: str) -> int | None:
        suffix = _COMBO_SUFFIX_RE.search(raw_name.strip())
        if suffix:
            num = suffix.group(1).zfill(2)
            if num in self._combo_by_num:
                return self._combo_by_num[num]

        norm = normalize(raw_name)
        if norm in ALIASES:
            return self._by_name.get(ALIASES[norm])

        return self._normalized.get(norm)

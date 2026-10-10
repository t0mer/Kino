"""Filesystem-safe slugs with simple Hebrew-to-Latin transliteration."""

from __future__ import annotations

import hashlib
import re
import unicodedata

MAX_LEN = 40

# Simple phonetic table (no niqqud; final forms mapped like their base letter).
_HEBREW = {
    "א": "", "ב": "b", "ג": "g", "ד": "d", "ה": "h", "ו": "v", "ז": "z",
    "ח": "ch", "ט": "t", "י": "y", "כ": "k", "ך": "k", "ל": "l", "מ": "m",
    "ם": "m", "נ": "n", "ן": "n", "ס": "s", "ע": "", "פ": "p", "ף": "f",
    "צ": "tz", "ץ": "tz", "ק": "k", "ר": "r", "ש": "sh", "ת": "t",
}  # fmt: skip
# Trailing ה / ו / י after a vowel-less letter read as vowels in names (נועה -> noa).
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")


def _translit(text: str) -> str:
    out: list[str] = []
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch in _HEBREW:
            val = _HEBREW[ch]
            prev_heb = i > 0 and chars[i - 1] in _HEBREW
            next_heb = i + 1 < len(chars) and chars[i + 1] in _HEBREW
            if ch == "ו" and prev_heb:
                val = "o"  # medial vav is usually a vowel
            elif ch == "ע" and prev_heb:
                val = "a"  # medial/final ayin is usually an "a" sound
            elif ch == "ה" and prev_heb and not next_heb:
                val = ""  # word-final he is silent (נועה -> noa)
            elif ch == "י" and prev_heb and not next_heb:
                val = "i"
            out.append(val)
        else:
            out.append(ch)
    return "".join(out)


def slugify(text: str, fallback: str = "item") -> str:
    """Return a lowercase slug of [a-z0-9-], 1..40 chars, never empty.

    If nothing usable remains, returns ``<fallback>-<8 hex of sha256(text)>``
    so distinct inputs stay distinct and the result is stable.
    """
    norm = unicodedata.normalize("NFKD", _translit(text))
    ascii_text = norm.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NOT_ALNUM.sub("-", ascii_text).strip("-")[:MAX_LEN].strip("-")
    if slug:
        return slug
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:8]
    return f"{fallback}-{digest}"

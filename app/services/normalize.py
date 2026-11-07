"""Metadata normalization.

Two jobs:
1. Strip *noise* that differs across platforms but doesn't change the recording
   (remaster tags, "feat." credits, punctuation, accents, case).
2. Detect *version markers* that DO change the recording (live, remix, karaoke...).
   These are returned separately so the matcher can refuse to swap versions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from unidecode import unidecode

# Tags that describe the same recording -> safe to drop.
_NOISE_WORDS = (
    r"remaster(?:ed)?(?:\s+\d{4})?|\d{4}\s+remaster(?:ed)?|digital remaster|"
    r"single version|album version|original version|stereo version|mono version|"
    r"explicit|clean|bonus track|deluxe(?: edition)?|radio version|from\s+.+|"
    r"original mix|original motion picture soundtrack|\d{4} version|version|"
    r"anniversary edition|expanded edition"
)

# Tags that change the recording -> keep, compare across sides.
_VERSION_PATTERNS: dict[str, str] = {
    "live": r"\blive\b|\bin concert\b|\bunplugged\b|\bat the\b.*\blive\b",
    "remix": r"\bremix(?:ed)?\b|\brework\b|\bvip\b|\bbootleg\b|\bflip\b|\bmashup\b",
    "acoustic": r"\bacoustic\b|\bstripped\b",
    "instrumental": r"\binstrumental\b|\bkaraoke\b|\bbacking track\b|\bminus one\b",
    "cover": r"\bcover(?:ed)?\b|\btribute\b|\bin the style of\b|\boriginally performed\b",
    "demo": r"\bdemo\b|\bsession\b|\balternate take\b|\bunreleased\b",
    "speed": r"\bsped up\b|\bspeed up\b|\bslowed\b|\breverb\b|\bnightcore\b|\b8d\b|\bpitched\b",
    "extended": r"\bextended\b|\bclub mix\b|\bdub\b",
}

# Version tags that are *dangerous* when they appear only on the candidate side.
DANGEROUS_ONE_SIDED = {"live", "remix", "instrumental", "cover", "speed", "demo", "acoustic"}

_FEAT_RE = re.compile(
    r"[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^\)\]]+)[\)\]]|"
    r"\s+(?:feat\.?|ft\.?|featuring)\s+(.+)$",
    re.IGNORECASE,
)
_BRACKET_RE = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
_DASH_SUFFIX_RE = re.compile(r"\s+[-–—]\s+(.+)$")
_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")
_NOISE_RE = re.compile(rf"^(?:{_NOISE_WORDS})$", re.IGNORECASE)


@dataclass(frozen=True)
class NormalizedTitle:
    clean: str  # canonical comparable title
    versions: frozenset[str] = frozenset()
    featured: tuple[str, ...] = field(default_factory=tuple)


def fold(text: str) -> str:
    """Lowercase, strip accents, drop punctuation, collapse whitespace."""
    text = unidecode(text or "").lower().replace("&", " and ")
    text = _NON_WORD_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _versions_in(fragment: str) -> set[str]:
    frag = fragment.lower()
    found = set()
    for name, pattern in _VERSION_PATTERNS.items():
        if re.search(pattern, frag):
            found.add(name)
    # "radio edit" is a noise tag, not a remix
    if "radio edit" in frag and "remix" not in frag:
        found.discard("remix")
    return found


def normalize_title(raw: str) -> NormalizedTitle:
    title = raw or ""
    versions: set[str] = set()
    featured: list[str] = []

    # 1. pull featured artists out of the title
    def _grab_feat(m: re.Match) -> str:
        names = (m.group(1) or m.group(2) or "").strip()
        if names:
            featured.extend(split_artists(names))
        return " "

    title = _FEAT_RE.sub(_grab_feat, title)

    # 2. bracketed annotations: keep versions, drop noise, keep unknown content
    def _handle_bracket(m: re.Match) -> str:
        inner = m.group(1).strip()
        v = _versions_in(inner)
        if v:
            versions.update(v)
            return " "
        if _NOISE_RE.match(inner) or "remaster" in inner.lower() or "radio edit" in inner.lower():
            return " "
        return f" {inner} "  # e.g. "(Taylor's Version)" or "(Interlude)" stays meaningful

    title = _BRACKET_RE.sub(_handle_bracket, title)

    # 3. " - Remastered 2011" / " - Live at Wembley" style suffixes
    m = _DASH_SUFFIX_RE.search(title)
    if m:
        suffix = m.group(1)
        v = _versions_in(suffix)
        if v:
            versions.update(v)
            title = title[: m.start()]
        elif _NOISE_RE.match(suffix.strip()) or "remaster" in suffix.lower() or "radio edit" in suffix.lower():
            title = title[: m.start()]
        # otherwise it's likely part of the real title ("Hello - Goodbye"); keep it

    clean = fold(title)
    if not clean:  # title was nothing but a tag, e.g. "(Intro)"
        clean = fold(raw)
    return NormalizedTitle(clean=clean, versions=frozenset(versions), featured=tuple(featured))


_ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|;|/|\s&\s|\sand\s|\sx\s|\sfeat\.?\s|\sft\.?\s|\swith\s)\s*", re.IGNORECASE)

# Names where a comma/ampersand is part of the name, not a separator.
_ATOMIC_ARTISTS = {
    "tyler, the creator", "earth, wind & fire", "crosby, stills & nash",
    "simon & garfunkel", "hall & oates", "florence + the machine", "ac/dc",
    "mumford & sons", "of monsters and men", "belle and sebastian",
    "emerson, lake & palmer", "years & years", "her's", "bob marley & the wailers",
}


def split_artists(raw: str) -> list[str]:
    if not raw:
        return []
    if raw.strip().lower() in _ATOMIC_ARTISTS:
        return [raw.strip()]
    parts = [p.strip() for p in _ARTIST_SPLIT_RE.split(raw) if p.strip()]
    return parts or [raw.strip()]


def normalize_artist(raw: str) -> str:
    name = fold(raw)
    return name[4:] if name.startswith("the ") and len(name) > 6 else name


def normalize_album(raw: str | None) -> str:
    if not raw:
        return ""
    # reuse title cleanup: strips "(Deluxe Edition)", "(Remastered)", etc.
    return normalize_title(raw).clean

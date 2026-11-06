"""Deterministic-first track matching.

score = weighted mean of available signals (title 40 / artist 30 / album 15 / duration 15).
Signals that are missing on either side are dropped and the remaining weights renormalised,
so sparse metadata is never punished as if it were a mismatch.

Safety rails (a wrong song is worse than a skipped song):
  * ISRC equality  -> instant HIGH
  * hard gates     -> title or artist too different => LOW no matter what else matches
  * version guard  -> candidate is live/remix/karaoke/... but source isn't (or vice versa) => penalty
  * ambiguity      -> two *different* songs scoring nearly the same => REVIEW, never auto
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from rapidfuzz import fuzz

from app.core.config import Settings
from app.core.schema import Candidate, Track
from app.services.normalize import (
    DANGEROUS_ONE_SIDED,
    normalize_album,
    normalize_artist,
    normalize_title,
    split_artists,
)

WEIGHTS = {"title": 0.40, "artist": 0.30, "album": 0.15, "duration": 0.15}

TITLE_GATE = 0.60
ARTIST_GATE = 0.55
VERSION_PENALTY = 0.35  # subtracted per dangerous one-sided version mismatch (capped)


class Confidence(str, Enum):
    HIGH = "high"  # auto-accept
    MEDIUM = "medium"  # needs review
    LOW = "low"  # unresolved


@dataclass
class ScoredCandidate:
    candidate: Candidate
    score: float
    signals: dict[str, float | None]
    flags: list[str] = field(default_factory=list)


@dataclass
class MatchOutcome:
    confidence: Confidence
    best: ScoredCandidate | None
    ranked: list[ScoredCandidate]
    reason: str


class Reranker(Protocol):
    """Optional LLM hook, only consulted for MEDIUM outcomes. Return a candidate id or None."""

    async def pick(self, track: Track, ranked: list[ScoredCandidate]) -> str | None: ...


# --------------------------------------------------------------------------- signals
def _title_sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    # blend strict + order-insensitive; guard against short strings matching by accident
    strict = fuzz.ratio(a, b) / 100
    loose = fuzz.token_sort_ratio(a, b) / 100
    return 0.6 * strict + 0.4 * loose


def _artist_sim(src: list[str], cand: list[str]) -> float | None:
    src_n = [normalize_artist(a) for a in src if a]
    cand_n = [normalize_artist(a) for a in cand if a]
    if not src_n or not cand_n:
        return None
    # primary artist must be present on the candidate side
    primary = max(fuzz.ratio(src_n[0], c) / 100 for c in cand_n)
    # coverage: how many source artists have a close counterpart
    hits = sum(1 for s in src_n if max(fuzz.ratio(s, c) for c in cand_n) >= 85)
    coverage = hits / len(src_n)
    return 0.7 * primary + 0.3 * coverage


def _album_sim(a: str | None, b: str | None) -> float | None:
    na, nb = normalize_album(a), normalize_album(b)
    if not na or not nb:
        return None
    return fuzz.token_set_ratio(na, nb) / 100


def _duration_sim(a: int | None, b: int | None) -> float | None:
    if not a or not b:
        return None
    diff = abs(a - b) / 1000
    if diff <= 3:
        return 1.0
    if diff >= 20:
        return 0.0
    return 1.0 - (diff - 3) / 17


# --------------------------------------------------------------------------- scoring
def score_candidate(track: Track, cand: Candidate) -> ScoredCandidate:
    flags: list[str] = []

    if track.isrc and cand.isrc and track.isrc == cand.isrc:
        return ScoredCandidate(cand, 1.0, {"isrc": 1.0}, ["isrc_exact"])

    src_t = normalize_title(track.title)
    cand_t = normalize_title(cand.title)

    # featured artists living in titles count as artists on that side
    src_artists = list(track.artists) + [a for a in src_t.featured if a not in track.artists]
    cand_artists = list(cand.artists) + [a for a in cand_t.featured if a not in cand.artists]

    signals: dict[str, float | None] = {
        "title": _title_sim(src_t.clean, cand_t.clean),
        "artist": _artist_sim(src_artists, cand_artists),
        "album": _album_sim(track.album, cand.album),
        "duration": _duration_sim(track.duration_ms, cand.duration_ms),
    }

    available = {k: v for k, v in signals.items() if v is not None}
    total_w = sum(WEIGHTS[k] for k in available)
    score = sum(WEIGHTS[k] * v for k, v in available.items()) / total_w if total_w else 0.0

    # hard gates
    if signals["title"] < TITLE_GATE:
        flags.append("title_gate")
        score = min(score, 0.5)
    if signals["artist"] is not None and signals["artist"] < ARTIST_GATE:
        flags.append("artist_gate")
        score = min(score, 0.5)

    # version guard: remix/live/karaoke on only one side
    one_sided = (src_t.versions ^ cand_t.versions) & DANGEROUS_ONE_SIDED
    if one_sided:
        flags.extend(f"version_mismatch:{v}" for v in sorted(one_sided))
        score -= min(VERSION_PENALTY * len(one_sided), 0.6)

    # Duration is the strongest anti-false-positive signal when everything else is close
    if signals["duration"] is not None and signals["duration"] == 0.0:
        flags.append("duration_far")
        score = min(score, 0.80)  # can still reach review, never auto-accept

    return ScoredCandidate(cand, max(0.0, min(1.0, score)), signals, flags)


def _same_song(a: Candidate, b: Candidate) -> bool:
    """Two catalog entries that are effectively the same recording (single vs album release)."""
    if a.isrc and b.isrc:
        return a.isrc == b.isrc
    ta, tb = normalize_title(a.title), normalize_title(b.title)
    pa = normalize_artist(a.artists[0]) if a.artists else ""
    pb = normalize_artist(b.artists[0]) if b.artists else ""
    return ta.clean == tb.clean and ta.versions == tb.versions and pa == pb


def decide(track: Track, candidates: list[Candidate], settings: Settings) -> MatchOutcome:
    if not candidates:
        return MatchOutcome(Confidence.LOW, None, [], "no candidates returned by destination")

    ranked = sorted((score_candidate(track, c) for c in candidates), key=lambda s: s.score, reverse=True)
    best = ranked[0]

    if "isrc_exact" in best.flags:
        return MatchOutcome(Confidence.HIGH, best, ranked, "ISRC exact match")

    if best.score < settings.review_threshold:
        why = ", ".join(best.flags) or f"best score {best.score:.2f} below review threshold"
        return MatchOutcome(Confidence.LOW, best, ranked, why)

    if best.score < settings.auto_accept_threshold:
        return MatchOutcome(Confidence.MEDIUM, best, ranked, f"score {best.score:.2f} needs review")

    # high score: check ambiguity against a genuinely different song
    for other in ranked[1:]:
        if best.score - other.score >= settings.ambiguity_margin:
            break
        if not _same_song(best.candidate, other.candidate):
            return MatchOutcome(
                Confidence.MEDIUM, best, ranked,
                f"ambiguous: '{other.candidate.title}' scored {other.score:.2f} vs {best.score:.2f}",
            )

    if best.flags:  # e.g. a version flag survived but score is still high -> don't auto-accept
        return MatchOutcome(Confidence.MEDIUM, best, ranked, "flags: " + ", ".join(best.flags))

    return MatchOutcome(Confidence.HIGH, best, ranked, f"score {best.score:.2f}")


def build_queries(track: Track) -> list[str]:
    """Search strings from most to least specific. Adapters try them in order until they get hits."""
    title = normalize_title(track.title).clean
    artist = normalize_artist(track.artists[0]) if track.artists else ""
    queries = []
    if artist:
        queries.append(f"{title} {artist}")
    if artist and track.album:
        queries.append(f"{title} {artist} {normalize_album(track.album)}")
    queries.append(title)
    # de-dupe preserving order
    seen, out = set(), []
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


__all__ = [
    "Confidence", "MatchOutcome", "ScoredCandidate", "Reranker",
    "decide", "score_candidate", "build_queries", "split_artists",
]

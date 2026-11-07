import pytest

from app.core.config import Settings
from app.core.schema import Candidate, Track
from app.services.matching import Confidence, build_queries, decide, score_candidate
from app.services.normalize import normalize_title, split_artists

S = Settings()


def T(title, artists=("The Weeknd",), album=None, dur=None, isrc=None):
    return Track(title=title, artists=list(artists), album=album, duration_ms=dur, isrc=isrc)


def C(id, title, artists=("The Weeknd",), album=None, dur=None, isrc=None):
    return Candidate(id=id, title=title, artists=list(artists), album=album, duration_ms=dur, isrc=isrc)


# ----------------------------------------------------------------- normalization
@pytest.mark.parametrize(
    "raw,clean,versions",
    [
        ("Blinding Lights - Remastered", "blinding lights", set()),
        ("Blinding Lights (Remastered 2011)", "blinding lights", set()),
        ("Blinding Lights - 2011 Remaster", "blinding lights", set()),
        ("Song (Explicit)", "song", set()),
        ("Song - Radio Edit", "song", set()),
        ("Song (Live at Wembley)", "song", {"live"}),
        ("Song - Live", "song", {"live"}),
        ("Song (Skrillex Remix)", "song", {"remix"}),
        ("Song - Karaoke Version", "song", {"instrumental"}),
        ("Song (Sped Up)", "song", {"speed"}),
        ("Hello - Goodbye", "hello goodbye", set()),  # dash is part of real title
        ("Café del Mar", "cafe del mar", set()),
        ("Song (From \"Frozen\")", "song", set()),
    ],
)
def test_normalize_title(raw, clean, versions):
    n = normalize_title(raw)
    assert n.clean == clean
    assert set(n.versions) == versions


def test_featured_artists_extracted():
    n = normalize_title("Song (feat. Daft Punk & Pharrell)")
    assert n.clean == "song"
    assert "Daft Punk" in n.featured and "Pharrell" in n.featured
    assert normalize_title("Song ft. Drake").featured == ("Drake",)


def test_split_artists():
    assert split_artists("Drake, Future") == ["Drake", "Future"]
    assert split_artists("Tyler, The Creator") == ["Tyler, The Creator"]
    assert split_artists("Simon & Garfunkel") == ["Simon & Garfunkel"]
    assert split_artists("A feat. B") == ["A", "B"]


# ----------------------------------------------------------------- matching
def test_remaster_tag_does_not_block_match():
    out = decide(
        T("Blinding Lights - Remastered", album="After Hours", dur=200_040),
        [C("1", "Blinding Lights", album="After Hours (Deluxe)", dur=200_000)],
        S,
    )
    assert out.confidence == Confidence.HIGH and out.best.candidate.id == "1"


def test_isrc_wins_even_when_titles_differ():
    out = decide(
        T("Weird Localized Title", isrc="USUG11904206"),
        [C("1", "Blinding Lights", isrc="USUG11904206"), C("2", "Weird Localized Title")],
        S,
    )
    assert out.confidence == Confidence.HIGH and out.best.candidate.id == "1"


def test_karaoke_impostor_is_never_auto_matched():
    out = decide(
        T("Blinding Lights", dur=200_000),
        [C("k", "Blinding Lights (Karaoke Version)", artists=("Karaoke Hits",), dur=200_000)],
        S,
    )
    assert out.confidence == Confidence.LOW


def test_remix_vs_original_prefers_original():
    out = decide(
        T("Blinding Lights", album="After Hours", dur=200_000),
        [
            C("r", "Blinding Lights (Remix)", dur=220_000),
            C("o", "Blinding Lights", album="After Hours", dur=200_000),
        ],
        S,
    )
    assert out.best.candidate.id == "o" and out.confidence == Confidence.HIGH


def test_source_remix_requires_remix_candidate():
    out = decide(
        T("Song (Skrillex Remix)", artists=("Artist",), dur=240_000),
        [C("o", "Song", artists=("Artist",), dur=200_000), C("r", "Song - Skrillex Remix", artists=("Artist",), dur=240_000)],
        S,
    )
    assert out.best.candidate.id == "r"


def test_live_version_not_swapped_for_studio():
    out = decide(T("Song - Live", artists=("Artist",)), [C("s", "Song", artists=("Artist",))], S)
    assert out.confidence != Confidence.HIGH


def test_wrong_artist_same_title_is_low():
    out = decide(T("Hurt", ("Nine Inch Nails",)), [C("1", "Hurt", ("Christina Aguilera",))], S)
    assert out.confidence == Confidence.LOW
    assert "artist_gate" in out.best.flags


def test_same_album_different_song_is_low():
    out = decide(
        T("Starboy", album="Starboy", dur=230_000),
        [C("1", "Reminder", album="Starboy", dur=218_000)],
        S,
    )
    assert out.confidence == Confidence.LOW


def test_missing_metadata_is_not_punished():
    out = decide(T("Blinding Lights"), [C("1", "Blinding Lights", album="After Hours", dur=200_000)], S)
    assert out.confidence == Confidence.HIGH


def test_duration_mismatch_blocks_auto_accept():
    out = decide(T("Song", ("A",), dur=180_000), [C("1", "Song", ("A",), dur=300_000)], S)
    assert out.confidence != Confidence.HIGH


def test_single_vs_album_release_is_not_ambiguous():
    out = decide(
        T("Blinding Lights", dur=200_000),
        [
            C("a", "Blinding Lights", album="After Hours", dur=200_000, isrc="USUG11904206"),
            C("b", "Blinding Lights", album="Blinding Lights - Single", dur=200_000, isrc="USUG11904206"),
        ],
        S,
    )
    assert out.confidence == Confidence.HIGH


def test_genuinely_ambiguous_goes_to_review():
    # no artist on the source + same title by two different artists => must NOT auto-pick
    out = decide(
        T("Hurt", artists=()),
        [C("1", "Hurt", ("Nine Inch Nails",)), C("2", "Hurt", ("Johnny Cash",))],
        S,
    )
    assert out.confidence == Confidence.MEDIUM
    assert "ambiguous" in out.reason


def test_clear_winner_over_near_title_is_not_ambiguous():
    out = decide(
        T("Intro", ("Artist",)),
        [C("1", "Intro", ("Artist",)), C("2", "Intro (Part 2)", ("Artist",))],
        S,
    )
    assert out.confidence == Confidence.HIGH and out.best.candidate.id == "1"


def test_no_candidates():
    assert decide(T("x"), [], S).confidence == Confidence.LOW


def test_featured_artist_in_title_matches_artist_list():
    sc = score_candidate(
        T("Get Lucky (feat. Pharrell Williams)", ("Daft Punk",)),
        C("1", "Get Lucky", ("Daft Punk", "Pharrell Williams")),
    )
    assert sc.score > 0.9


def test_build_queries_ordered_and_unique():
    q = build_queries(T("Blinding Lights - Remastered", album="After Hours"))
    assert q[0] == "blinding lights weeknd"
    assert len(q) == len(set(q))

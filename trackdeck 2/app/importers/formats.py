"""Parse user-supplied playlist files into the universal schema, and export back out.

Supported inputs (auto-detected):
  * Spotify personal data export  (Playlist1.json -> {"playlists": [...]})
  * TrackDeck JSON                ({"name": "...", "tracks": [...]} or a list of those)
  * CSV                           (Exportify-style headers and common aliases)
  * TXT                           ("Title | Artist1; Artist2 | Album | mm:ss | ISRC"  or  "Artist - Title")
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Literal

from app.core.schema import ParsedPlaylist, Track
from app.services.normalize import split_artists


class ImportError_(ValueError):
    """Raised with a user-presentable message when a file can't be parsed."""


DashOrder = Literal["artist_title", "title_artist"]


# ------------------------------------------------------------------------ helpers
def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ImportError_("Could not decode file; please upload UTF-8 text.")


def _parse_duration(value: str | int | float | None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    v = str(value).strip()
    if re.fullmatch(r"\d+:\d{1,2}(:\d{1,2})?", v):
        parts = [int(p) for p in v.split(":")]
        secs = parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]
        return secs * 1000 or None
    if re.fullmatch(r"\d+", v):
        n = int(v)
        return n if n > 1000 else n * 1000 or None  # <1000 -> assume seconds
    return None


def _artists(value) -> list[str]:
    if isinstance(value, list):
        return [str(a) for a in value if a]
    return split_artists(str(value)) if value else []


# ------------------------------------------------------------------------ spotify export
def _from_spotify_export(data: dict) -> list[ParsedPlaylist]:
    out = []
    for pl in data.get("playlists", []):
        tracks = []
        for item in pl.get("items", []):
            t = item.get("track")
            if not t or item.get("episode"):  # podcasts can't be matched to songs
                continue
            title = t.get("trackName")
            if not title:
                continue
            uri = t.get("trackUri") or ""
            tracks.append(
                Track(
                    title=title,
                    artists=_artists(t.get("artistName")),
                    album=t.get("albumName"),
                    source_id=uri.rsplit(":", 1)[-1] if uri else None,
                    position=len(tracks),
                )
            )
        out.append(ParsedPlaylist(name=pl.get("name") or "Untitled playlist", tracks=tracks))
    return out


# ------------------------------------------------------------------------ trackdeck json
def _from_native_json(data) -> list[ParsedPlaylist]:
    items = data if isinstance(data, list) else [data]
    out = []
    for pl in items:
        tracks = []
        for raw in pl.get("tracks", []):
            try:
                tracks.append(Track(**{**raw, "position": len(tracks)}))
            except Exception:
                continue
        out.append(ParsedPlaylist(name=pl.get("name") or "Untitled playlist", tracks=tracks))
    return out


# ------------------------------------------------------------------------ csv
_CSV_ALIASES = {
    "title": ("track name", "title", "name", "song", "track", "song name"),
    "artists": ("artist name(s)", "artist names", "artist name", "artists", "artist"),
    "album": ("album name", "album"),
    "duration": ("duration (ms)", "duration_ms", "duration", "length", "time"),
    "isrc": ("isrc",),
    "id": ("track uri", "track id", "uri", "id"),
}


def _pick(row: dict[str, str], key: str) -> str | None:
    for alias in _CSV_ALIASES[key]:
        if alias in row and row[alias] not in (None, ""):
            return row[alias]
    return None


def _from_csv(text: str, name: str) -> list[ParsedPlaylist]:
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ImportError_("CSV has no header row.")
    tracks = []
    for raw in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        title = _pick(row, "title")
        if not title:
            continue
        uri = _pick(row, "id")
        tracks.append(
            Track(
                title=title,
                artists=_artists(_pick(row, "artists")),
                album=_pick(row, "album"),
                duration_ms=_parse_duration(_pick(row, "duration")),
                isrc=_pick(row, "isrc"),
                source_id=uri.rsplit(":", 1)[-1] if uri else None,
                position=len(tracks),
            )
        )
    if not tracks:
        raise ImportError_("No tracks found in CSV. Expected a title column like 'Track Name'.")
    return [ParsedPlaylist(name=name, tracks=tracks)]


# ------------------------------------------------------------------------ txt
def _from_txt(text: str, name: str, dash_order: DashOrder) -> list[ParsedPlaylist]:
    tracks = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^\s*\d+[\.\)]\s+", "", line)  # "12. Song" numbering
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            title, artist = parts[0], parts[1] if len(parts) > 1 else ""
            album = parts[2] if len(parts) > 2 and parts[2] else None
            dur = _parse_duration(parts[3]) if len(parts) > 3 else None
            isrc = parts[4] if len(parts) > 4 and parts[4] else None
        else:
            m = re.split(r"\s+[-–—]\s+", line, maxsplit=1)
            if len(m) == 2:
                a, b = m
                artist, title = (a, b) if dash_order == "artist_title" else (b, a)
            else:
                title, artist = line, ""
            album = dur = isrc = None
        if not title:
            continue
        tracks.append(
            Track(title=title, artists=_artists(artist), album=album, duration_ms=dur, isrc=isrc, position=len(tracks))
        )
    if not tracks:
        raise ImportError_("No tracks found in text file.")
    return [ParsedPlaylist(name=name, tracks=tracks)]


def _looks_like_csv(text: str, filename: str) -> bool:
    if (filename or "").lower().endswith(".csv"):
        return True
    first = text.strip().splitlines()[0].lower() if text.strip() else ""
    if not any(d in first for d in (",", ";", "\t")):
        return False
    header_cells = {c.strip().strip('"') for c in re.split(r"[,;\t]", first)}
    return bool(header_cells & set(_CSV_ALIASES["title"]))


# ------------------------------------------------------------------------ entrypoint
def parse_playlists(
    raw: bytes, filename: str = "playlist", dash_order: DashOrder = "artist_title"
) -> list[ParsedPlaylist]:
    text = _decode(raw)
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename or "playlist") or "playlist"
    stripped = text.lstrip()

    if stripped[:1] in "{[":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ImportError_(f"Invalid JSON: {e.msg}") from e
        if isinstance(data, dict) and "playlists" in data:
            return _from_spotify_export(data)
        if isinstance(data, (dict, list)):
            return _from_native_json(data)
        raise ImportError_("Unrecognised JSON structure.")

    if _looks_like_csv(text, filename):
        return _from_csv(text, stem)

    return _from_txt(text, stem, dash_order)


# ------------------------------------------------------------------------ export
def _fmt_duration(ms: int | None) -> str:
    if not ms:
        return ""
    s = round(ms / 1000)
    return f"{s // 60}:{s % 60:02d}"


def export_txt(name: str, tracks: list[Track]) -> str:
    lines = [f"# {name}", "# format: Title | Artist1; Artist2 | Album | mm:ss | ISRC"]
    for t in tracks:
        lines.append(
            " | ".join(
                [t.title, "; ".join(t.artists), t.album or "", _fmt_duration(t.duration_ms), t.isrc or ""]
            ).rstrip(" |")
        )
    return "\n".join(lines) + "\n"


def export_csv(tracks: list[Track]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Track Name", "Artist Name(s)", "Album Name", "Duration (ms)", "ISRC"])
    for t in tracks:
        w.writerow([t.title, "; ".join(t.artists), t.album or "", t.duration_ms or "", t.isrc or ""])
    return buf.getvalue()


def export_json(name: str, tracks: list[Track]) -> str:
    return json.dumps({"name": name, "tracks": [t.model_dump() for t in tracks]}, indent=2, ensure_ascii=False)

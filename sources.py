"""Data sources for termradio. Every source is free and needs no API key.

Radio:    Radio Browser (~50k stations), SomaFM, a hand-verified curated list
Music:    Audius (full tracks), Internet Archive (concerts, netlabels, 78s), ccMixter
Podcasts: Apple Podcasts directory (search + charts) + the podcast's own RSS feed,
          LibriVox audiobooks and Old Time Radio via Internet Archive
"""
from __future__ import annotations

import http.client
import json
import random
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Callable

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) termradio/1.0"
APP = "termradio"
TIMEOUT = 15
http.client._MAXLINE = 1 << 20  # ccMixter sends one very long header line


@dataclass
class Item:
    kind: str  # station | track | episode | podcast | album | folder
    title: str
    subtitle: str = ""  # artist / country / author
    info: str = ""  # genre / tags / description
    meta: str = ""  # bitrate / duration / date
    source: str = ""
    url: str = ""  # set when directly playable
    ref: dict = field(default_factory=dict)  # set when it expands into more items

    @property
    def playable(self) -> bool:
        return bool(self.url)

    @property
    def key(self) -> str:
        return self.url or json.dumps(self.ref, sort_keys=True)

    def to_dict(self) -> dict:
        return asdict(self)


def item_from_dict(d: dict) -> Item:
    fields = Item.__dataclass_fields__
    return Item(**{k: v for k, v in d.items() if k in fields})


# ---------------------------------------------------------------- http helpers

def get(url: str, params: dict | None = None, timeout: int = TIMEOUT) -> bytes:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_json(url: str, params: dict | None = None):
    return json.loads(get(url, params).decode("utf-8", "replace"))


def fmt_dur(seconds) -> str:
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_hms(text: str) -> int:
    """'01:02:03' / '62:03' / '3723' -> seconds."""
    try:
        parts = [int(float(p)) for p in str(text).strip().split(":")]
    except ValueError:
        return 0
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def clean(text: str, n: int = 200) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:n]


# ------------------------------------------------------------ playlist resolve

def resolve_stream(url: str) -> str:
    """Radio URLs are sometimes .pls/.m3u playlists; return the first real stream."""
    low = url.lower().split("?")[0]
    if not low.endswith((".pls", ".m3u", ".m3u8x", ".asx", ".xspf")):
        return url
    try:
        body = get(url, timeout=8)[:65536].decode("utf-8", "replace")
    except Exception:
        return url
    found = re.findall(r"(https?://[^\s\"'<>]+)", body)
    return found[0] if found else url


def follow_redirects(url: str) -> str:
    """Podcast URLs often chain 4-6 tracking redirects; VLC gives up on those, so resolve them here."""
    for method in ("HEAD", "GET"):  # some hosts refuse HEAD; GET only reads the headers before closing
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.geturl()
        except Exception:
            continue
    return url


# --------------------------------------------------------------- curated radio

CURATED = [
    # (name, genre, country, url) -- all verified reachable
    ("Radio Paradise - Main Mix", "eclectic rock", "US", "http://stream.radioparadise.com/mp3-192"),
    ("Radio Paradise - Mellow", "mellow, chill", "US", "http://stream.radioparadise.com/mellow-192"),
    ("Radio Paradise - Rock", "rock", "US", "http://stream.radioparadise.com/rock-192"),
    ("Radio Paradise - Global", "world", "US", "http://stream.radioparadise.com/global-192"),
    ("KEXP 90.3 Seattle", "indie, alternative", "US", "https://kexp.streamguys1.com/kexp160.aac"),
    ("WFMU Freeform", "freeform, eclectic", "US", "https://stream0.wfmu.org/freeform-128k"),
    ("KQED Public Radio", "news, talk", "US", "https://streams.kqed.org/kqedradio"),
    ("BBC World Service", "news, talk", "UK", "https://stream.live.vc.bbcmedia.co.uk/bbc_world_service"),
    ("NTS Radio 1", "eclectic, underground", "UK", "https://stream-relay-geo.ntslive.net/stream"),
    ("NTS Radio 2", "eclectic, underground", "UK", "https://stream-relay-geo.ntslive.net/stream2"),
    ("FIP", "eclectic", "FR", "https://icecast.radiofrance.fr/fip-midfi.mp3"),
    ("FIP Jazz", "jazz", "FR", "https://icecast.radiofrance.fr/fipjazz-midfi.mp3"),
    ("FIP Groove", "funk, soul, groove", "FR", "https://icecast.radiofrance.fr/fipgroove-midfi.mp3"),
    ("FIP Electro", "electronic", "FR", "https://icecast.radiofrance.fr/fipelectro-midfi.mp3"),
    ("FIP World", "world", "FR", "https://icecast.radiofrance.fr/fipworld-midfi.mp3"),
    ("FIP Reggae", "reggae", "FR", "https://icecast.radiofrance.fr/fipreggae-midfi.mp3"),
    ("FIP Rock", "rock", "FR", "https://icecast.radiofrance.fr/fiprock-midfi.mp3"),
    ("France Culture", "talk, culture", "FR", "https://icecast.radiofrance.fr/franceculture-midfi.mp3"),
    ("Radio Swiss Jazz", "jazz", "CH", "https://stream.srg-ssr.ch/m/rsj/mp3_128"),
    ("Radio Swiss Classic", "classical", "CH", "https://stream.srg-ssr.ch/m/rsc_de/mp3_128"),
    ("FluxFM Chillhop", "lofi, chillhop", "DE", "https://streams.fluxfm.de/Chillhop/mp3-320/streams.fluxfm.de/"),
    ("FluxFM Lounge", "lounge", "DE", "https://streams.fluxfm.de/lounge/mp3-320/streams.fluxfm.de/"),
    ("FluxFM Jazzradio", "jazz", "DE", "https://streams.fluxfm.de/jazzradio/mp3-320/streams.fluxfm.de/"),
    ("Nightride FM", "synthwave", "--", "https://stream.nightride.fm/nightride.mp3"),
    ("Nightride Chillsynth", "chillsynth", "--", "https://stream.nightride.fm/chillsynth.mp3"),
    ("Nightride Darksynth", "darksynth", "--", "https://stream.nightride.fm/darksynth.mp3"),
    ("Plaza One", "vaporwave, future funk", "--", "https://radio.plaza.one/mp3"),
    ("181.fm Beatles", "beatles, 60s", "US", "https://listen.181fm.com/181-beatles_128k.mp3"),
] + [
    (f"0nlineradio {g if g[0].isdigit() else g.title()}", g, "DE", f"https://0n-{g}.radionetz.de/0n-{g}.mp3")
    for g in ["80s", "90s", "70s", "60s", "oldies", "gold", "rock", "indie", "jazz", "blues",
              "lounge", "chillout", "reggae", "country", "disco", "dance", "house", "techno",
              "latin", "kpop", "schlager"]
]


def curated() -> list[Item]:
    return [Item("station", n, c, g, "", "curated", u) for n, g, c, u in CURATED]


# ---------------------------------------------------------------------- SomaFM

def somafm() -> list[Item]:
    data = get_json("https://somafm.com/channels.json")
    out = []
    for ch in data.get("channels", []):
        cid = ch["id"]
        out.append(Item(
            "station", f"SomaFM {ch.get('title', cid)}", f"{ch.get('listeners', '?')} listening",
            f"{ch.get('genre', '')} - {clean(ch.get('description', ''), 90)}", "mp3 128k", "somafm",
            f"https://ice1.somafm.com/{cid}-128-mp3",
        ))
    out.sort(key=lambda i: -int(re.sub(r"\D", "", i.subtitle) or 0))
    return out


# --------------------------------------------------------------- Radio Browser

_rb_server: str | None = None


def rb_base() -> str:
    global _rb_server
    if _rb_server:
        return _rb_server
    hosts = []
    try:
        hosts = list({s["name"] for s in get_json("https://all.api.radio-browser.info/json/servers")})
    except Exception:
        pass
    random.shuffle(hosts)
    for h in hosts + ["de1.api.radio-browser.info", "de2.api.radio-browser.info", "fi1.api.radio-browser.info"]:
        try:
            get(f"https://{h}/json/stats", timeout=6)
            _rb_server = f"https://{h}"
            return _rb_server
        except Exception:
            continue
    raise RuntimeError("No Radio Browser server reachable")


def _rb_station(s: dict) -> Item:
    tags = ", ".join(t for t in (s.get("tags") or "").split(",")[:6] if t)
    br = f"{s.get('codec', '')} {s.get('bitrate') or ''}k".strip() if s.get("bitrate") else s.get("codec", "")
    return Item(
        "station", (s.get("name") or "").strip() or "(unnamed)", s.get("country") or s.get("countrycode") or "",
        tags, br, "radio-browser", s.get("url_resolved") or s.get("url") or "",
        {"uuid": s.get("stationuuid", "")},
    )


def rb_stations(limit: int = 250, **filters) -> list[Item]:
    params = {"hidebroken": "true", "limit": limit, "order": "clickcount", "reverse": "true"}
    params.update({k: v for k, v in filters.items() if v not in (None, "")})
    data = get_json(f"{rb_base()}/json/stations/search", params)
    seen, out = set(), []
    for s in data:
        it = _rb_station(s)
        if it.url and it.url not in seen:
            seen.add(it.url)
            out.append(it)
    return out


def rb_click(uuid: str) -> None:
    """Courtesy ping so Radio Browser's popularity stats stay accurate."""
    if uuid:
        try:
            get(f"{rb_base()}/json/url/{uuid}", timeout=5)
        except Exception:
            pass


def rb_folders(what: str) -> list[Item]:
    """Browse lists: tags (genres), countries, languages."""
    endpoint = {"tags": "tags", "countries": "countries", "languages": "languages"}[what]
    data = get_json(f"{rb_base()}/json/{endpoint}",
                    {"order": "stationcount", "reverse": "true", "hidebroken": "true", "limit": 500})
    out = []
    for d in data:
        n = d.get("stationcount", 0)
        if n < 5 or not d.get("name"):
            continue
        if what == "countries":
            ref = {"t": "rb", "countrycode": d.get("iso_3166_1", "")}
        elif what == "languages":
            ref = {"t": "rb", "language": d["name"], "languageExact": "true"}
        else:
            ref = {"t": "rb", "tag": d["name"], "tagExact": "true"}
        out.append(Item("folder", d["name"], f"{n} stations", "", "", "radio-browser", "", ref))
    return out


# ----------------------------------------------------------------------- Audius

AUDIUS = "https://api.audius.co"
AUDIUS_GENRES = [
    "Electronic", "Hip-Hop/Rap", "Lo-Fi", "Pop", "Rock", "Alternative", "R&B/Soul", "Ambient",
    "House", "Deep House", "Techno", "Trance", "Drum & Bass", "Dubstep", "Trap", "Downtempo",
    "Jazz", "Classical", "Acoustic", "Folk", "Country", "Latin", "Reggae", "Dancehall",
    "Funk", "Blues", "Metal", "Punk", "Experimental", "Soundtrack", "World", "Disco",
    "Hyperpop", "Vaporwave", "Future Bass", "Moombahton",
]


def _audius_track(t: dict) -> Item | None:
    if not t.get("is_streamable", True) or t.get("stream_conditions") or t.get("is_delete"):
        return None
    user = (t.get("user") or {}).get("name", "")
    plays = t.get("play_count") or 0
    return Item(
        "track", t.get("title", ""), user, " / ".join(x for x in (t.get("genre"), t.get("mood")) if x),
        f"{fmt_dur(t.get('duration'))}  {plays:,} plays", "audius",
        f"{AUDIUS}/v1/tracks/{t['id']}/stream?app_name={APP}",
    )


def _audius_tracks(path: str, params: dict | None = None) -> list[Item]:
    p = {"app_name": APP}
    p.update(params or {})
    data = get_json(f"{AUDIUS}{path}", p).get("data") or []
    return [i for i in (_audius_track(t) for t in data) if i]


def audius_trending(genre: str = "", time: str = "week") -> list[Item]:
    return _audius_tracks("/v1/tracks/trending", {"genre": genre, "time": time, "limit": 100} if genre
                          else {"time": time, "limit": 100})


def audius_underground() -> list[Item]:
    return _audius_tracks("/v1/tracks/trending/underground", {"limit": 100})


def audius_search(q: str) -> list[Item]:
    return _audius_tracks("/v1/tracks/search", {"query": q, "limit": 60})


def audius_playlists(q: str = "") -> list[Item]:
    if q:
        data = get_json(f"{AUDIUS}/v1/playlists/search", {"query": q, "app_name": APP, "limit": 40}).get("data") or []
    else:
        data = get_json(f"{AUDIUS}/v1/playlists/trending", {"app_name": APP, "limit": 60}).get("data") or []
    return [Item("album", p.get("playlist_name", ""), (p.get("user") or {}).get("name", ""),
                 clean(p.get("description", ""), 80), f"{p.get('track_count', '?')} tracks", "audius",
                 "", {"t": "audius_playlist", "id": p["id"]}) for p in data]


# ------------------------------------------------------------- Internet Archive

IA_COLLECTIONS = {
    "Live concerts (etree)": "collection:etree",
    "Grateful Dead live": "collection:GratefulDead",
    "Netlabels (CC music)": "collection:netlabels",
    "78 RPM records (1900s-50s)": "collection:78rpm",
    "Jazz": "mediatype:audio AND subject:jazz AND NOT collection:librivoxaudio",
    "Classical": "mediatype:audio AND subject:classical AND NOT collection:librivoxaudio",
    "Electronic": "collection:netlabels AND subject:electronic",
    "Ambient": "collection:netlabels AND subject:ambient",
    "Hip hop": "collection:hiphopmixtapes",
}
IA_SPOKEN = {
    "Old Time Radio shows": "collection:oldtimeradio",
    "LibriVox audiobooks": "collection:librivoxaudio",
}


def ia_search(q: str, sort: str = "downloads desc", rows: int = 150) -> list[Item]:
    q = f"({q}) AND NOT mediatype:collection"
    qs = urllib.parse.urlencode([("q", q), ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "creator"),
                                 ("fl[]", "date"), ("fl[]", "downloads"), ("sort[]", sort),
                                 ("rows", rows), ("output", "json")])
    data = get_json(f"https://archive.org/advancedsearch.php?{qs}")
    out = []
    for d in data.get("response", {}).get("docs", []):
        creator = d.get("creator", "")
        if isinstance(creator, list):
            creator = ", ".join(creator[:2])
        out.append(Item("album", str(d.get("title", d["identifier"])), str(creator), "",
                        f"{str(d.get('date', ''))[:10]}  {d.get('downloads', 0):,} dl", "archive.org",
                        "", {"t": "ia_item", "id": d["identifier"]}))
    return out


def ia_tracks(identifier: str) -> list[Item]:
    meta = get_json(f"https://archive.org/metadata/{urllib.parse.quote(identifier)}")
    files = meta.get("files", [])
    album = (meta.get("metadata") or {}).get("title", identifier)
    artist = (meta.get("metadata") or {}).get("creator", "")
    if isinstance(artist, list):
        artist = ", ".join(artist[:2])

    def pick(pred):
        return [f for f in files if pred(f)]

    chosen = (pick(lambda f: f.get("format") == "VBR MP3")
              or pick(lambda f: "MP3" in f.get("format", ""))
              or pick(lambda f: f["name"].lower().endswith((".mp3", ".ogg", ".m4a", ".flac"))))

    def order(f):
        tr = re.sub(r"\D", "", str(f.get("track", "")).split("/")[0])
        return (int(tr) if tr else 9999, f["name"])

    if not chosen:
        raise RuntimeError("No playable audio files in this item")
    out = []
    for f in sorted(chosen, key=order):
        dur = f.get("length", "")
        secs = parse_hms(dur) if ":" in str(dur) else dur
        out.append(Item("track", f.get("title") or f["name"].rsplit(".", 1)[0], f.get("creator") or artist,
                        album, fmt_dur(secs), "archive.org",
                        f"https://archive.org/download/{urllib.parse.quote(identifier)}/{urllib.parse.quote(f['name'])}"))
    return out


# ---------------------------------------------------------------------- ccMixter

CC_TAGS = ["chill", "ambient", "instrumental", "electronic", "hip_hop", "downtempo", "piano",
           "guitar", "trip_hop", "jazz", "female_vocals", "experimental", "cinematic", "funk", "rock"]


def ccmixter(tags: str = "", search: str = "") -> list[Item]:
    params = {"f": "json", "limit": 100, "sort": "rank"}
    if tags:
        params["tags"] = tags
    if search:
        params.update(search=search, search_type="any")
    out = []
    for u in get_json("https://ccmixter.org/api/query", params):
        mp3 = next((f.get("download_url") for f in u.get("files", [])
                    if (f.get("download_url") or "").lower().endswith(".mp3")), None)
        if not mp3:
            continue
        dur = next((f.get("file_format_info", {}).get("ps") for f in u.get("files", [])), "")
        tags_ = ", ".join((u.get("upload_extra") or {}).get("usertags", "").split(",")[:4])
        out.append(Item("track", u.get("upload_name", ""), u.get("user_name", ""), tags_, dur or "",
                        "ccmixter", mp3))
    return out


# ------------------------------------------------------------------- Podcasts

ITUNES_GENRES = {
    "All": "", "News": 1489, "True Crime": 1488, "Comedy": 1303, "Society & Culture": 1324,
    "Business": 1321, "Technology": 1318, "Science": 1533, "History": 1487, "Education": 1304,
    "Health & Fitness": 1512, "Sports": 1545, "Arts": 1301, "Music": 1310, "TV & Film": 1309,
    "Fiction": 1483, "Religion & Spirituality": 1314, "Kids & Family": 1305, "Leisure": 1502,
    "Government": 1511,
}


def podcast_charts(country: str, genre_id) -> list[Item]:
    g = f"/genre={genre_id}" if genre_id else ""
    data = get_json(f"https://itunes.apple.com/{country.lower()}/rss/toppodcasts/limit=100{g}/json")
    out = []
    for n, e in enumerate(data.get("feed", {}).get("entry", []) or [], 1):
        cat = (e.get("category") or {}).get("attributes", {}).get("label", "")
        out.append(Item("podcast", e["im:name"]["label"], e.get("im:artist", {}).get("label", ""), cat,
                        f"#{n}", "apple charts", "", {"t": "itunes_id", "id": e["id"]["attributes"]["im:id"]}))
    return out


def podcast_search(q: str) -> list[Item]:
    data = get_json("https://itunes.apple.com/search", {"media": "podcast", "term": q, "limit": 100})
    return [Item("podcast", r.get("collectionName", ""), r.get("artistName", ""), r.get("primaryGenreName", ""),
                 f"{r.get('trackCount', '?')} eps", "apple", "", {"t": "feed", "url": r["feedUrl"]})
            for r in data.get("results", []) if r.get("feedUrl")]


ITNS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


def podcast_episodes(feed_url: str) -> list[Item]:
    root = ET.fromstring(get(feed_url, timeout=25))
    chan = root.find("channel")
    if chan is None:
        return []
    show = chan.findtext("title", "")
    out = []
    for it in chan.findall("item")[:500]:
        enc = it.find("enclosure")
        url = enc.get("url") if enc is not None else None
        if not url:
            continue
        dur = it.findtext(f"{ITNS}duration", "")
        secs = parse_hms(dur) if dur else 0
        date = " ".join((it.findtext("pubDate", "") or "").split()[1:4])
        out.append(Item("episode", clean(it.findtext("title", ""), 150), show,
                        clean(it.findtext("description", "") or it.findtext(f"{ITNS}summary", ""), 160),
                        f"{date}  {fmt_dur(secs) if secs else ''}".strip(), "rss", url))
    return out


def itunes_feed(itunes_id: str) -> str:
    res = get_json("https://itunes.apple.com/lookup", {"id": itunes_id}).get("results") or []
    if not res or not res[0].get("feedUrl"):
        raise RuntimeError("This podcast has no public RSS feed")
    return res[0]["feedUrl"]


# --------------------------------------------------------------- expand refs

def expand(item: Item) -> list[Item]:
    ref = item.ref or {}
    t = ref.get("t")
    if t == "rb":
        return rb_stations(**{k: v for k, v in ref.items() if k != "t"})
    if t == "ia_item":
        return ia_tracks(ref["id"])
    if t == "audius_playlist":
        return _audius_tracks(f"/v1/playlists/{ref['id']}/tracks")
    if t == "feed":
        return podcast_episodes(ref["url"])
    if t == "itunes_id":
        return podcast_episodes(itunes_feed(ref["id"]))
    raise RuntimeError(f"Don't know how to open {item.title!r}")


def search_all(loaders: list[Callable[[], list[Item]]]) -> list[Item]:
    """Run several searches concurrently, interleave results, ignore the ones that fail."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(len(loaders)) as ex:
        futures = [ex.submit(f) for f in loaders]
        results = []
        for f in futures:
            try:
                results.append(f.result())
            except Exception:
                results.append([])
    out = []
    for i in range(max((len(r) for r in results), default=0)):
        for r in results:
            if i < len(r):
                out.append(r[i])
    return out

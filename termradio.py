"""termradio - radio, music and podcasts in your terminal.

    python termradio.py                 # start the app
    python termradio.py --country IN    # podcast charts for another country (US by default)

Keys:  enter play/open   backspace back   / search   space pause   s stop
       n / p next/prev   + / - volume     m mute     [ / ] seek -15s/+30s   , / . seek -5s/+5s
       click or drag the progress bar to jump anywhere in a track / episode
       f favourite       c copy share ID  t sleep timer    1-5 tabs   q quit
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import DescendantFocus
from textual.widgets import (Button, DataTable, Footer, Header, Input, OptionList, Static, TabbedContent,
                             TabPane)
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

import sources as S
from player import Player, PlayerError

STATE_FILE = Path.home() / ".termradio" / "state.json"
SLEEP_STEPS = [0, 15, 30, 60, 90]

Loader = Callable[[], list[S.Item]]


def system_copy(text: str) -> bool:
    """Put text on the OS clipboard (the terminal's OSC 52 copy doesn't work everywhere, e.g. old conhost)."""
    if sys.platform == "win32":
        cmds = [["clip"]]
    elif sys.platform == "darwin":
        cmds = [["pbcopy"]]
    else:
        cmds = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]]
    for cmd in cmds:
        try:
            subprocess.run(cmd, input=text.encode(), check=True, timeout=3,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return True
        except Exception:
            continue
    return False


class Store:
    """Favourites, history, volume and podcast resume positions in one JSON file."""

    def __init__(self) -> None:
        self.data = {"volume": 70, "country": "US", "favorites": [], "history": [], "positions": {}}
        try:
            self.data.update(json.loads(STATE_FILE.read_text("utf-8")))
        except Exception:
            pass
        self._fav_keys = {S.item_from_dict(d).key for d in self.data["favorites"]}

    def save(self) -> None:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1), "utf-8")
            tmp.replace(STATE_FILE)
        except Exception:
            pass

    def items(self, name: str) -> list[S.Item]:
        return [S.item_from_dict(d) for d in self.data[name]]

    def is_fav(self, item: S.Item) -> bool:
        return item.key in self._fav_keys

    def toggle_fav(self, item: S.Item) -> bool:
        if self.is_fav(item):
            self.data["favorites"] = [d for d in self.data["favorites"] if S.item_from_dict(d).key != item.key]
            self._fav_keys.discard(item.key)
            added = False
        else:
            self.data["favorites"].insert(0, item.to_dict())
            self._fav_keys.add(item.key)
            added = True
        self.save()
        return added

    def add_history(self, item: S.Item) -> None:
        h = [d for d in self.data["history"] if S.item_from_dict(d).key != item.key]
        self.data["history"] = [item.to_dict()] + h[:299]
        self.save()


# ------------------------------------------------------------------ widgets

class ItemTable(DataTable):
    BINDINGS = [Binding("escape", "back", "Back", show=False)]

    def action_back(self) -> None:
        self.browser.back()

    @property
    def browser(self) -> "Browser":
        return next(a for a in self.ancestors if isinstance(a, Browser))


class Browser(Vertical):
    """Sidebar of categories + search box + result table, with back history."""

    # On the Browser (not the table) so backspace also goes back from the sidebar; the search box
    # handles its own backspace first, so typing still works.
    BINDINGS = [Binding("backspace", "back", "Back", show=False)]
    MAX_HISTORY = 50

    def __init__(self, categories: list[tuple[str | None, str]], load: Callable[[str], list[S.Item]],
                 search: Callable[[str], list[S.Item]], placeholder: str, **kw) -> None:
        super().__init__(**kw)
        self.categories, self._load, self._search, self.placeholder = categories, load, search, placeholder
        self.items: list[S.Item] = []
        self.title_text = ""
        self.stack: list[tuple[str, list[S.Item], int, int | None]] = []  # title, items, row, sidebar
        self.busy = False  # not `loading`: that is Widget.loading, whose overlay steals focus and blocks back
        self.refocus = False

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield OptionList(*[Option(label, id=key, disabled=key is None) for key, label in self.categories],
                             classes="sidebar")
            with Vertical(classes="main"):
                yield Input(placeholder=self.placeholder, classes="search")
                with Horizontal(classes="bar"):
                    yield Static("", classes="crumb")
                    yield Button("☆ Fav", id="fav-btn", compact=True, tooltip="Add / remove favourite (f)")
                    yield Button("⧉ Copy ID", id="copy-btn", compact=True,
                                 tooltip="Copy a shareable ID (c); paste one in any search box to open it")
                yield ItemTable(cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        t = self.query_one(ItemTable)
        cols = [t.add_column(label, width=width) for label, width in
                (("", 1), ("Title", 42), ("Artist / Where", 20), ("Info", 30), ("", 18), ("Source", 12))]
        self.icon_col = cols[0]
        for btn in self.query(Button):
            btn.can_focus = False  # clicking keeps focus (and the highlighted row) where it was
        first = next((k for k, _ in self.categories if k), None)
        if first:
            sidebar = self.query_one(OptionList)
            sidebar.highlighted = next(i for i, (k, _) in enumerate(self.categories) if k == first)
            self.open_category(first)

    @property
    def table(self) -> ItemTable:
        return self.query_one(ItemTable)

    def crumb(self, text: str) -> None:
        self.query_one(".crumb", Static).update(Text(text))

    # -- loading
    def load_into(self, title: str, fn: Loader, push: bool = False) -> None:
        self.crumb(f"{title}  ·  loading…")
        self.busy = True
        # Refilling the table can briefly hide it, which moves focus to the search box; give it back after.
        self.refocus = self.app.focused is self.table or self.refocus
        # Every navigation (search, category, drill-in) remembers the view it leaves, so back returns to it.
        has_view = bool(self.title_text) and not self.title_text.endswith("failed")
        prev = (self.title_text, self.items, self.table.cursor_row,
                self.query_one(OptionList).highlighted) if push and has_view else None

        def work() -> None:
            worker = get_current_worker()
            try:
                items = fn()
            except Exception as e:  # network errors, bad feeds, …
                if not worker.is_cancelled:
                    self.app.call_from_thread(self._failed, title, e, prev)
                return
            if not worker.is_cancelled:
                self.app.call_from_thread(self._show, title, items, prev)

        self.run_worker(work, thread=True, exclusive=True, group="load")

    def _failed(self, title: str, err: Exception, prev) -> None:
        self.busy = False
        if self.refocus:
            self.refocus = False
            self.call_after_refresh(self.table.focus)
        self.crumb(f"{self.title_text}" if prev else f"{title}  ·  failed")
        self.app.notify(f"{title}: {err}", title="Couldn't load", severity="error", timeout=6)

    def _show(self, title: str, items: list[S.Item], prev=None) -> None:
        if prev is not None:
            self.stack.append(prev)
            del self.stack[:-self.MAX_HISTORY]
        self.items, self.title_text, self.busy = items, title, False
        self.refresh_rows()
        if self.refocus:
            self.refocus = False
            self.call_after_refresh(self.table.focus)
        depth = "  ‹ backspace" if self.stack else ""
        self.crumb(f"{title}  ·  {len(items)} results{depth}")
        if items:
            self.table.move_cursor(row=0)

    def icon(self, it: S.Item) -> Text:
        app: TermRadio = self.app  # type: ignore[assignment]
        playing = app.current is not None and it.key == app.current.key
        return Text("▶" if playing else "★" if app.store.is_fav(it) else "♪" if it.playable else "›")

    def refresh_icons(self) -> None:
        """Cheap in-place update of the ▶/★ column (a full refill would disturb focus)."""
        self.update_buttons()
        t = self.table
        if t.row_count != len(self.items):
            return self.refresh_rows()
        for i, it in enumerate(self.items):
            t.update_cell(str(i), self.icon_col, self.icon(it))

    def refresh_rows(self) -> None:
        t = self.table
        keep, had_focus = t.cursor_row, self.app.focused is t
        t.clear()
        for i, it in enumerate(self.items):
            t.add_row(self.icon(it), Text(it.title[:60]), Text(it.subtitle[:40]), Text(it.info[:80]),
                      Text(it.meta), Text(it.source, style="dim"), key=str(i))
        if self.items:
            t.move_cursor(row=min(keep, len(self.items) - 1), animate=False)
        if had_focus:
            self.call_after_refresh(t.focus)
        self.update_buttons()

    def action_back(self) -> None:
        self.back()

    def back(self) -> None:
        if self.busy:  # back while loading = cancel, and stay on the list that is still shown
            self.workers.cancel_group(self, "load")
            self.busy = False
            depth = "  ‹ backspace" if self.stack else ""
            self.crumb(f"{self.title_text}  ·  {len(self.items)} results{depth}" if self.title_text else "")
            return
        if self.stack:
            title, items, cursor, side = self.stack.pop()
            self.items, self.title_text = items, title
            self.refresh_rows()
            if items:
                self.table.move_cursor(row=min(cursor, len(items) - 1), animate=False)
            if side is not None:
                self.query_one(OptionList).highlighted = side
            depth = "  ‹ backspace" if self.stack else ""
            self.crumb(f"{title}  ·  {len(items)} results{depth}")

    def key_escape(self) -> None:
        self.table.focus()

    def open_category(self, key: str, push: bool = False) -> None:
        label = next(l for k, l in self.categories if k == key).strip()
        self.load_into(label, lambda: self._load(key), push=push)

    def highlighted(self) -> S.Item | None:
        r = self.table.cursor_row
        return self.items[r] if self.items and 0 <= r < len(self.items) else None

    @property
    def tab(self) -> str:
        return next(a for a in self.ancestors if isinstance(a, TabPane)).id or ""

    def category_item(self) -> S.Item | None:
        """The highlighted sidebar category as a favouritable/shareable item (e.g. Radio › Jazz)."""
        opt = self.query_one(OptionList)
        if opt.highlighted is None or self.tab in ("favs", "history"):
            return None
        key = opt.get_option_at_index(opt.highlighted).id
        if not key:
            return None
        label = next(l for k, l in self.categories if k == key).strip()
        return S.Item("folder", label, f"{self.tab.title()} category", "", "", "termradio", "",
                      {"t": "category", "tab": self.tab, "key": key})

    def target(self) -> S.Item | None:
        """What f / Fav / Copy ID act on: the sidebar category if the sidebar has focus, else the row."""
        f = self.app.focused
        if isinstance(f, OptionList) and self in f.ancestors:
            return self.category_item()
        return self.highlighted()

    def update_buttons(self) -> None:
        app: TermRadio = self.app  # type: ignore[assignment]
        it = self.target() or app.current
        fav = self.query_one("#fav-btn", Button)
        fav.label = "★ Unfav" if it and app.store.is_fav(it) else "☆ Fav"
        fav.disabled = self.query_one("#copy-btn", Button).disabled = it is None

    # -- events
    @on(OptionList.OptionSelected)
    def _category(self, ev: OptionList.OptionSelected) -> None:
        if ev.option_id:
            self.refocus = True
            self.open_category(ev.option_id, push=True)

    @on(OptionList.OptionHighlighted)
    @on(DataTable.RowHighlighted)
    @on(DescendantFocus)
    def _selection_moved(self) -> None:
        self.update_buttons()

    @on(Button.Pressed, "#fav-btn")
    def _fav_pressed(self, ev: Button.Pressed) -> None:
        ev.stop()
        self.app.action_fav()  # type: ignore[attr-defined]

    @on(Button.Pressed, "#copy-btn")
    def _copy_pressed(self, ev: Button.Pressed) -> None:
        ev.stop()
        self.app.action_copy_id()  # type: ignore[attr-defined]

    @on(Input.Submitted)
    def _search_submitted(self, ev: Input.Submitted) -> None:
        q = ev.value.strip()
        if S.is_share_id(q):
            self.refocus = True
            self.load_into("Shared item", lambda: [S.from_share_id(q)], push=True)
        elif q:
            self.refocus = True
            self.load_into(f'Search "{q}"', lambda: self._search(q), push=True)

    @on(DataTable.RowSelected)
    def _row(self, ev: DataTable.RowSelected) -> None:
        idx = int(ev.row_key.value)
        it = self.items[idx]
        app: TermRadio = self.app  # type: ignore[assignment]
        if it.playable:
            playable = [x for x in self.items if x.playable]
            app.play(it, playable, playable.index(it))
        elif it.ref.get("t") == "category":
            b = app.browser(it.ref["tab"])
            self.load_into(it.title, lambda: b._load(it.ref["key"]), push=True)
        else:
            self.load_into(it.title, lambda: S.expand(it), push=True)


class NowPlaying(Static):
    """Status line; click or drag its progress bar to seek."""

    def _bar_ms(self, event) -> int | None:
        """Track position under the mouse, if it is over the progress bar (line 2 of the widget)."""
        app: TermRadio = self.app  # type: ignore[assignment]
        dragging = app.dragging is not None
        off = event.get_content_offset_capture(self) if dragging else event.get_content_offset(self)
        if app.bar is None or off is None:
            return None
        x0, width, length = app.bar
        if not dragging and (off.y != 1 or not x0 <= off.x < x0 + width):
            return None
        return int(min(1.0, max(0.0, (off.x - x0 + 0.5) / width)) * length)

    def _redraw(self) -> None:
        app: TermRadio = self.app  # type: ignore[assignment]
        self.update(app.status_text(app.player.state if app.current else "idle"))

    def on_mouse_down(self, event) -> None:
        ms = self._bar_ms(event)
        if ms is not None:
            self.app.dragging = ms  # type: ignore[attr-defined]
            self.capture_mouse()
            self._redraw()

    def on_mouse_move(self, event) -> None:
        if self.app.dragging is not None:  # type: ignore[attr-defined]
            ms = self._bar_ms(event)
            if ms is not None:
                self.app.dragging = ms  # type: ignore[attr-defined]
                self._redraw()

    def on_mouse_up(self, event) -> None:
        app: TermRadio = self.app  # type: ignore[assignment]
        if app.dragging is not None:
            ms, app.dragging = app.dragging, None
            self.release_mouse()
            app.player.seek_to(ms)
            self._redraw()


# ------------------------------------------------------------------ app

def radio_categories() -> list[tuple[str | None, str]]:
    genres = ["pop", "rock", "jazz", "classical", "electronic", "dance", "house", "techno", "trance",
              "ambient", "chillout", "lounge", "lofi", "hip hop", "rnb", "soul", "funk", "blues",
              "country", "folk", "indie", "alternative", "metal", "punk", "reggae", "latin", "salsa",
              "bollywood", "hindi", "tamil", "punjabi", "kpop", "jpop", "anime", "arabic", "80s", "90s",
              "70s", "60s", "oldies", "synthwave", "drum and bass", "dubstep", "soundtrack", "piano",
              "meditation", "world", "christian", "news", "talk", "sports", "comedy"]
    return ([(None, "── Quick picks"), ("curated", "★ Curated (verified)"), ("somafm", "SomaFM (ad-free)"),
             ("trend", "Trending now"), ("clicks", "Most played"), ("votes", "Top voted"),
             ("near:IN", "India"), ("near:US", "USA"), ("near:GB", "UK"),
             (None, "── Browse everything"), ("all:tags", "All genres / tags…"),
             ("all:countries", "All countries…"), ("all:languages", "All languages…"),
             (None, "── Genres")]
            + [(f"tag:{g}", g.title()) for g in genres])


def load_radio(key: str) -> list[S.Item]:
    if key == "curated":
        return S.curated()
    if key == "somafm":
        return S.somafm()
    if key == "trend":
        return S.rb_stations(order="clicktrend")
    if key == "clicks":
        return S.rb_stations()
    if key == "votes":
        return S.rb_stations(order="votes")
    kind, _, val = key.partition(":")
    if kind == "near":
        return S.rb_stations(countrycode=val)
    if kind == "all":
        return S.rb_folders(val)
    return S.rb_stations(tag=val)


def search_radio(q: str) -> list[S.Item]:
    """Plain text searches names; also supports tag:jazz  country:IN  lang:hindi (combinable)."""
    filters, words = {}, []
    for tok in q.split():
        k, sep, v = tok.partition(":")
        if sep and k in ("tag", "genre"):
            filters["tag"] = v.replace("_", " ")
        elif sep and k in ("country", "cc"):
            filters["countrycode"] = v.upper()
        elif sep and k in ("lang", "language"):
            filters["language"] = v
        else:
            words.append(tok)
    if words:
        filters["name"] = " ".join(words)
    local = [i for i in S.curated() if q.lower() in (i.title + i.info).lower()] if not filters.keys() - {"name"} else []
    return local + S.rb_stations(**filters)


def music_categories() -> list[tuple[str | None, str]]:
    return ([(None, "── Audius (full tracks)"), ("au:trend", "Trending this week"),
             ("au:month", "Trending this month"), ("au:under", "Underground trending"),
             ("au:playlists", "Trending playlists…")]
            + [(f"au:g:{g}", f"  {g}") for g in S.AUDIUS_GENRES]
            + [(None, "── Internet Archive")]
            + [(f"ia:{name}", name) for name in S.IA_COLLECTIONS]
            + [(None, "── ccMixter (Creative Commons)"), ("cc:", "Top ranked")]
            + [(f"cc:{t}", t.replace("_", " ").title()) for t in S.CC_TAGS])


def load_music(key: str) -> list[S.Item]:
    src, _, val = key.partition(":")
    if src == "au":
        if val == "trend":
            return S.audius_trending()
        if val == "month":
            return S.audius_trending(time="month")
        if val == "under":
            return S.audius_underground()
        if val == "playlists":
            return S.audius_playlists()
        return S.audius_trending(genre=val[2:])
    if src == "ia":
        return S.ia_search(S.IA_COLLECTIONS[val])
    return S.ccmixter(tags=val)


def search_music(q: str) -> list[S.Item]:
    return S.search_all([
        lambda: S.audius_search(q),
        lambda: S.ccmixter(search=q),
        lambda: S.ia_search(f"({q}) AND mediatype:audio AND NOT collection:librivoxaudio", rows=60),
        lambda: S.audius_playlists(q),
    ], q)


class TermRadio(App):
    TITLE = "termradio"
    SUB_TITLE = "radio · music · podcasts"
    CSS = """
    TabbedContent { height: 1fr; }
    TabPane { height: 1fr; padding: 0; }
    Browser { height: 1fr; }
    .sidebar { width: 26; height: 1fr; border: none; border-right: tall $panel; }
    .main { height: 1fr; }
    .search { margin: 0 0; }
    .bar { height: 1; }
    .crumb { width: 1fr; height: 1; padding: 0 1; color: $text-muted; }
    .bar Button { margin-left: 1; min-width: 9; }
    ItemTable { height: 1fr; }
    NowPlaying { height: 3; padding: 0 1; background: $boost; border-top: hkey $accent; }
    """
    BINDINGS = [
        Binding("slash", "search", "Search"),
        Binding("space", "pause", "Pause"),
        Binding("s", "stop", "Stop"),
        Binding("n", "next", "Next"),
        Binding("p", "prev", "Prev", show=False),
        Binding("plus,equals_sign", "vol(5)", "Vol+"),
        Binding("minus,underscore", "vol(-5)", "Vol-"),
        Binding("m", "mute", "Mute", show=False),
        Binding("left_square_bracket", "seek(-15000)", "-15s", show=False),
        Binding("right_square_bracket", "seek(30000)", "+30s", show=False),
        Binding("comma", "seek(-5000)", "-5s", show=False),
        Binding("full_stop", "seek(5000)", "+5s", show=False),
        Binding("f", "fav", "Fav"),
        Binding("c", "copy_id", "Copy ID"),
        Binding("t", "sleep", "Sleep"),
        Binding("1", "tab('radio')", show=False),
        Binding("2", "tab('music')", show=False),
        Binding("3", "tab('podcasts')", show=False),
        Binding("4", "tab('favs')", show=False),
        Binding("5", "tab('history')", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, country: str | None = None) -> None:
        super().__init__()
        self.store = Store()
        if country:
            self.store.data["country"] = country.upper()
        self.country = self.store.data.get("country", "US")
        self.player = Player(self.store.data.get("volume", 70))
        self.current: S.Item | None = None
        self.queue: list[S.Item] = []
        self.qi = 0
        self.sleep_at = 0.0
        self.sleep_step = 0
        self.retries = 0
        self.started = 0.0
        self.last_state = "idle"
        self.active_tab = "radio"
        self.bar: tuple[int, int, int] | None = None  # progress bar: first column, width, track length (ms)
        self.dragging: int | None = None  # position (ms) being dragged to on the progress bar

    # -- layout
    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="radio"):
            with TabPane("1 Radio", id="radio"):
                yield Browser(radio_categories(), load_radio, search_radio,
                              "Search 50,000+ stations…  (also tag:jazz  country:IN  lang:hindi)")
            with TabPane("2 Music", id="music"):
                yield Browser(music_categories(), load_music, search_music,
                              "Search tracks, artists, albums across Audius, Archive.org, ccMixter…")
            with TabPane("3 Podcasts", id="podcasts"):
                yield Browser(self.podcast_categories(), self.load_podcasts, self.search_podcasts,
                              "Search any podcast (Apple directory) or audiobook…")
            with TabPane("4 Favourites", id="favs"):
                yield Browser([("fav:all", "All"), ("fav:station", "Stations"), ("fav:track", "Tracks"),
                               ("fav:podcast", "Podcasts"), ("fav:episode", "Episodes"), ("fav:album", "Albums & playlists"),
                               ("fav:folder", "Groups & categories")],
                              self.load_favs, lambda q: self.filter_store("favorites", q),
                              "Filter favourites…  (f / ☆ Fav on a row or category; paste a shared ID)")
            with TabPane("5 History", id="history"):
                yield Browser([("hist:all", "Recently played")], lambda k: self.store.items("history"),
                              lambda q: self.filter_store("history", q), "Filter history…")
        yield NowPlaying()
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(1.0, self.tick)
        self.tick()
        self.call_after_refresh(lambda: self._focus_pane(self.query_one(TabbedContent).active))

    def on_unmount(self) -> None:
        self.remember_position()
        self.store.data["volume"] = self.player.volume
        self.store.save()
        self.player.close()

    # -- podcast tab
    def podcast_categories(self) -> list[tuple[str | None, str]]:
        return ([(None, f"── Top charts ({self.country})")]
                + [(f"chart:{name}", name) for name in S.ITUNES_GENRES]
                + [(None, "── Spoken word (Archive.org)")]
                + [(f"ia:{name}", name) for name in S.IA_SPOKEN])

    def load_podcasts(self, key: str) -> list[S.Item]:
        kind, _, val = key.partition(":")
        if kind == "chart":
            return S.podcast_charts(self.country, S.ITUNES_GENRES[val])
        return S.ia_search(S.IA_SPOKEN[val])

    def search_podcasts(self, q: str) -> list[S.Item]:
        return S.search_all([
            lambda: S.podcast_search(q),
            lambda: S.ia_search(f"({q}) AND (collection:librivoxaudio OR collection:oldtimeradio)", rows=40),
        ], q)

    # -- favourites / history
    def load_favs(self, key: str) -> list[S.Item]:
        kind = key.split(":")[1]
        items = self.store.items("favorites")
        return items if kind == "all" else [i for i in items if i.kind == kind]

    def filter_store(self, name: str, q: str) -> list[S.Item]:
        q = q.lower()
        return [i for i in self.store.items(name) if q in f"{i.title} {i.subtitle} {i.info}".lower()]

    def browser(self, tab: str | None = None) -> Browser:
        tab = tab or self.query_one(TabbedContent).active
        return self.query_one(f"#{tab} Browser", Browser)

    @on(TabbedContent.TabActivated)
    def _tab_changed(self, ev: TabbedContent.TabActivated) -> None:
        tab = ev.pane.id
        # Focusing a widget inside a pane re-fires TabActivated; only react to real tab switches.
        if tab == self.active_tab:
            return
        self.active_tab = tab
        if tab in ("favs", "history"):
            b = self.browser(tab)
            b.open_category(next(k for k, _ in b.categories if k))
        self.call_after_refresh(self._focus_pane, tab)

    def _focus_pane(self, tab: str) -> None:
        b, f = self.browser(tab), self.focused
        if f is None or b not in f.ancestors:  # don't steal focus from e.g. the search box
            b.table.focus()

    # -- playback
    def play(self, item: S.Item, queue: list[S.Item] | None = None, index: int = 0) -> None:
        self.remember_position()
        if queue is not None:
            self.queue, self.qi = queue, index
        self.current, self.retries = item, 0
        self.store.add_history(item)
        self.start_stream(item)
        self.refresh_all_rows()

    def start_stream(self, item: S.Item) -> None:
        start = 0
        if item.kind == "episode":
            start = self.store.data["positions"].get(item.url, 0)

        def work() -> None:
            url = S.resolve_stream(item.url) if item.kind == "station" else S.follow_redirects(item.url)
            if item.ref.get("uuid"):
                S.rb_click(item.ref["uuid"])
            if self.current is item:
                self.call_from_thread(self._start, url, start)

        self.started = time.monotonic()
        self.run_worker(work, thread=True, exclusive=True, group="play")

    def _start(self, url: str, start_ms: int) -> None:
        self.player.play(url, start_ms)
        self.started = time.monotonic()
        if start_ms > 30000:
            self.notify(f"Resuming at {S.fmt_dur(start_ms // 1000)}", timeout=3)

    def remember_position(self) -> None:
        it = self.current
        if it and it.kind == "episode" and self.player.length_ms:
            pos, length = self.player.time_ms, self.player.length_ms
            positions = self.store.data["positions"]
            if 30000 < pos < length - 60000:
                positions[it.url] = pos
            else:
                positions.pop(it.url, None)
            if len(positions) > 500:
                for k in list(positions)[:100]:
                    positions.pop(k)
            self.store.save()

    def refresh_all_rows(self) -> None:
        for b in self.query(Browser):
            b.refresh_icons()

    def tick(self) -> None:
        p, it = self.player, self.current
        state = p.state if it else "idle"

        if it and state in ("ended", "error") and time.monotonic() - self.started > 2:
            if it.kind == "station" and self.retries < 3:
                self.retries += 1
                self.notify(f"Stream dropped, reconnecting ({self.retries}/3)…", timeout=3)
                self.start_stream(it)
                state = "connecting"
            elif it.kind == "station":
                self.notify(f"Can't play {it.title}. Try another station.", severity="error")
                self.current = None
                self.refresh_all_rows()
            elif state == "ended" and self.last_state != "ended":
                if it.kind == "episode":
                    self.store.data["positions"].pop(it.url, None)
                self.action_next(auto=True)
            elif state == "error" and self.last_state != "error":
                self.notify(f"Can't play {it.title}, skipping.", severity="warning")
                self.action_next(auto=True)
        self.last_state = state
        if state == "playing" and time.monotonic() - self.started > 15:
            self.retries = 0  # only give up on a station after 3 drops in a row

        if it and it.kind == "episode" and state == "playing" and int(time.monotonic()) % 15 == 0:
            self.remember_position()

        if self.sleep_at and time.time() >= self.sleep_at:
            self.sleep_at, self.sleep_step = 0.0, 0
            self.remember_position()
            p.stop()
            self.notify("Sleep timer: stopped playback. Good night.")

        self.query_one(NowPlaying).update(self.status_text(state))

    def status_text(self, state: str) -> Text:
        p, it = self.player, self.current
        t = Text()
        if not it:
            t.append("Nothing playing. ", style="bold")
            t.append("Pick a category on the left, or press / to search. Enter plays.", style="dim")
            return t
        icon = {"playing": "▶", "paused": "⏸", "connecting": "…", "buffering": "…", "stopped": "■",
                "ended": "■", "error": "✖"}.get(state, "…")
        t.append(f"{icon} {it.title}", style="bold")
        if it.subtitle:
            t.append(f"  ·  {it.subtitle}", style="dim")
        t.append("\n")
        line = Text()  # second line: now-playing, progress bar, volume…
        np = p.now_playing()
        if np and np != it.title:
            line.append(f"  ♫ {np[:48]}", style="italic")
        elif state in ("connecting", "buffering"):
            line.append("  connecting…", style="dim")
        tail = Text()
        vol = "muted" if p.muted else f"vol {p.volume}%"
        tail.append(f"   {vol}", style="dim")
        if len(self.queue) > 1 and it.kind != "station":
            tail.append(f"   {self.qi + 1}/{len(self.queue)}", style="dim")
        if self.sleep_at:
            tail.append(f"   sleep {max(0, int((self.sleep_at - time.time()) // 60)) + 1}m", style="dim")
        length, pos = p.length_ms, p.time_ms
        self.bar = None
        if length > 0 and it.kind != "station":
            if self.dragging is not None:
                pos = self.dragging
            line.append(f"   {S.fmt_dur(pos // 1000)} ", style="bold" if self.dragging is not None else "dim")
            end = Text(f" {S.fmt_dur(length // 1000)}", style="dim")
            # The bar takes all the room that's left, so each cell is as few seconds as possible.
            avail = self.query_one(NowPlaying).content_size.width
            width = max(10, avail - line.cell_len - end.cell_len - tail.cell_len - 1)
            self.bar = (line.cell_len, width, length)
            filled = min(width - 1, int(width * pos / length))
            line.append("━" * filled, style="bold")
            line.append("●", style="bold")
            line.append("─" * (width - filled - 1), style="dim")
            line.append_text(end)
        elif pos > 0:
            line.append(f"   live · {S.fmt_dur(pos // 1000)}", style="dim")
        t.append_text(line)
        t.append_text(tail)
        return t

    # -- actions
    def action_search(self) -> None:
        self.browser().query_one(Input).focus()

    def action_pause(self) -> None:
        if self.current and self.player.state in ("stopped", "ended", "idle"):
            self.start_stream(self.current)
        else:
            self.player.toggle_pause()

    def action_stop(self) -> None:
        self.remember_position()
        self.player.stop()

    def action_next(self, auto: bool = False) -> None:
        if self.queue and self.qi + 1 < len(self.queue):
            self.qi += 1
            self.play(self.queue[self.qi])
        elif auto:
            self.player.stop()
            self.notify("End of list.", timeout=3)

    def action_prev(self) -> None:
        if self.player.time_ms > 5000 and self.current and self.current.kind != "station":
            self.player.seek(-self.player.time_ms)
        elif self.queue and self.qi > 0:
            self.qi -= 1
            self.play(self.queue[self.qi])

    def action_vol(self, delta: int) -> None:
        self.player.volume = self.player.volume + delta
        self.store.data["volume"] = self.player.volume
        self.tick()

    def action_mute(self) -> None:
        self.player.toggle_mute()

    def action_seek(self, delta: int) -> None:
        self.player.seek(delta)

    def action_fav(self) -> None:
        b = self.browser()
        it = b.target() or self.current
        if not it or it.kind == "folder" and not it.ref:
            self.notify("Nothing selected to favourite.", severity="warning", timeout=2)
            return
        added = self.store.toggle_fav(it)
        self.notify(("★ Added " if added else "Removed ") + it.title, timeout=2)
        if not added and self.query_one(TabbedContent).active == "favs":
            b.items = [i for i in b.items if i.key != it.key]
        self.refresh_all_rows()

    def action_copy_id(self) -> None:
        it = self.browser().target() or self.current
        if not it:
            self.notify("Nothing selected to copy.", severity="warning", timeout=2)
            return
        sid = S.share_id(it)
        self.copy_to_clipboard(sid)  # OSC 52, for terminals / SSH sessions that support it
        system_copy(sid)
        self.notify(f"{sid}\nPaste it into any search box to open it.", title=f"Copied ID: {it.title}", timeout=6)

    def action_sleep(self) -> None:
        self.sleep_step = (self.sleep_step + 1) % len(SLEEP_STEPS)
        mins = SLEEP_STEPS[self.sleep_step]
        self.sleep_at = time.time() + mins * 60 if mins else 0.0
        self.notify(f"Sleep timer: {mins} min" if mins else "Sleep timer off", timeout=2)

    def action_tab(self, tab: str) -> None:
        # Drop focus first: hiding a pane that holds focus makes Textual hop focus (and the tab) around.
        self.screen.set_focus(None)
        self.query_one(TabbedContent).active = tab


def main() -> None:
    ap = argparse.ArgumentParser(description="Radio, music and podcasts in your terminal.")
    ap.add_argument("--country", help="2-letter country for podcast charts, e.g. IN, US, GB (remembered)")
    args = ap.parse_args()
    try:
        app = TermRadio(args.country)
    except PlayerError as e:
        sys.exit(f"termradio can't play audio:\n{e}")
    app.run()


if __name__ == "__main__":
    main()

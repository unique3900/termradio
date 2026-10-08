"""Drives the real app headless (Textual Pilot) with a fake player and fake sources: no VLC, no network."""
import asyncio

import pytest
from textual.widgets import Input, OptionList

from termradio import app as A
from termradio import sources as S

TRACKS = [S.Item("track", t, source=src, url=f"http://x/{t}") for t, src in
          [("charlie", "Audius"), ("alpha", "ccMixter"), ("bravo", "Archive")]]


class FakePlayer:
    def __init__(self, volume=70):
        self.volume, self.muted, self.state, self.time_ms, self.length_ms = volume, False, "idle", 0, 0

    def now_playing(self):
        return ""

    def close(self):
        pass


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(A, "Player", FakePlayer)
    monkeypatch.setattr(A, "load_radio", lambda key: list(TRACKS))
    monkeypatch.setattr(A, "load_music", lambda key: [])
    monkeypatch.setattr(A.TermRadio, "load_podcasts", lambda self, key: [])
    return A.TermRadio()


async def settle(pilot):
    await pilot.pause()
    await asyncio.sleep(0.05)
    await pilot.pause()


def titles(browser):
    return [i.title for i in browser.items]


async def new_playlist_via_a(pilot, name):
    await pilot.press("a")
    await settle(pilot)
    picker = pilot.app.screen
    assert isinstance(picker, A.PlaylistPicker)
    opts = picker.query_one(OptionList)
    opts.highlighted = opts.option_count - 1  # "+ New playlist…"
    await pilot.press("enter")
    await settle(pilot)
    assert isinstance(pilot.app.screen, A.NamePrompt)
    pilot.app.screen.query_one(Input).value = name
    await pilot.press("enter")
    await settle(pilot)


async def add_to_existing(pilot, index):
    await pilot.press("a")
    await settle(pilot)
    pilot.app.screen.query_one(OptionList).highlighted = index
    await pilot.press("enter")
    await settle(pilot)


async def test_create_add_reorder_sort_remove(app):
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot)
        radio = app.browser("radio")
        assert titles(radio) == ["charlie", "alpha", "bravo"]

        await new_playlist_via_a(pilot, "Mix")  # adds "charlie"
        assert app.store.playlist_names() == ["Mix"]
        await pilot.press("down")
        await add_to_existing(pilot, 0)  # "alpha"
        await pilot.press("down")
        await add_to_existing(pilot, 0)  # "bravo"
        await pilot.press("up")
        await add_to_existing(pilot, 0)  # "alpha" again: skipped
        assert [i.title for i in app.store.playlist_items("Mix")] == ["charlie", "alpha", "bravo"]

        await pilot.press("6")
        await settle(pilot)
        pl = app.browser("playlists")
        assert pl.list_id == "pl:Mix"
        assert titles(pl) == ["charlie", "alpha", "bravo"]
        assert "sort: custom" in str(pl.query_one(".crumb").render())

        app.set_focus(pl.table)
        pl.table.move_cursor(row=0)
        await pilot.press("shift+down")
        await settle(pilot)
        assert titles(pl) == ["alpha", "charlie", "bravo"]
        assert pl.highlighted().title == "charlie"  # cursor follows the moved row

        await pilot.press("o")  # -> name
        await settle(pilot)
        assert titles(pl) == ["alpha", "bravo", "charlie"]
        assert "sort: name" in str(pl.query_one(".crumb").render())
        await pilot.press("shift+up")  # not allowed outside custom order
        await settle(pilot)
        assert [i.title for i in app.store.playlist_items("Mix")] == ["alpha", "charlie", "bravo"]

        for _ in A.SORT_MODES[1:]:  # back round to custom
            await pilot.press("o")
        await settle(pilot)
        assert app.store.get_sort("pl:Mix") == "custom"

        pl.table.move_cursor(row=1)
        await pilot.press("delete")
        await settle(pilot)
        assert titles(pl) == ["alpha", "bravo"]
        assert [i.title for i in app.store.playlist_items("Mix")] == ["alpha", "bravo"]


async def test_new_rename_delete_from_sidebar(app):
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot)
        await pilot.press("6")
        await settle(pilot)
        pl = app.browser("playlists")
        sidebar = pl.query_one(OptionList)
        assert [k for k, _ in pl.categories] == [A.NEW_PLAYLIST]

        app.set_focus(sidebar)
        sidebar.highlighted = 0
        await pilot.press("enter")  # "+ New playlist…"
        await settle(pilot)
        app.screen.query_one(Input).value = "Chill"
        await pilot.press("enter")
        await settle(pilot)
        assert app.store.playlist_names() == ["Chill"]
        assert pl.view_key == "pl:Chill"

        app.set_focus(sidebar)
        sidebar.highlighted = 0
        await pilot.press("r")
        await settle(pilot)
        assert isinstance(app.screen, A.NamePrompt)
        app.screen.query_one(Input).value = "Chill out"
        await pilot.press("enter")
        await settle(pilot)
        assert app.store.playlist_names() == ["Chill out"]
        assert pl.view_key == "pl:Chill out"

        app.set_focus(sidebar)
        sidebar.highlighted = 0
        await pilot.press("delete")
        await settle(pilot)
        assert isinstance(app.screen, A.Confirm)
        await pilot.press("n")
        await settle(pilot)
        assert app.store.playlist_names() == ["Chill out"]

        app.set_focus(sidebar)
        sidebar.highlighted = 0
        await pilot.press("delete")
        await settle(pilot)
        await pilot.press("y")
        await settle(pilot)
        assert app.store.playlist_names() == []
        assert [k for k, _ in pl.categories] == [A.NEW_PLAYLIST]


async def test_favourites_sort_and_reorder_in_filtered_view(app):
    app.store.toggle_fav(S.Item("station", "Zeta FM", url="http://s/z"))
    for t in TRACKS:
        app.store.toggle_fav(t)  # stored: bravo, alpha, charlie, Zeta FM
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot)
        await pilot.press("4")
        await settle(pilot)
        fav = app.browser("favs")
        assert titles(fav) == ["bravo", "alpha", "charlie", "Zeta FM"]

        await pilot.press("o")
        await settle(pilot)
        assert titles(fav) == ["alpha", "bravo", "charlie", "Zeta FM"]
        for _ in A.SORT_MODES[1:]:  # back round to custom
            await pilot.press("o")
        await settle(pilot)

        # Tracks only (filtered view): moving the last track up past its on-screen neighbour
        sidebar = fav.query_one(OptionList)
        sidebar.highlighted = [k for k, _ in fav.categories].index("fav:track")
        app.set_focus(sidebar)
        await pilot.press("enter")
        await settle(pilot)
        assert titles(fav) == ["bravo", "alpha", "charlie"]
        app.set_focus(fav.table)
        fav.table.move_cursor(row=2)
        await pilot.press("shift+up")
        await settle(pilot)
        assert titles(fav) == ["bravo", "charlie", "alpha"]
        assert [i.title for i in app.store.items("favorites")] == ["bravo", "charlie", "alpha", "Zeta FM"]

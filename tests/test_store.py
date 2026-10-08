import json

import pytest

from termradio import app as A
from termradio import sources as S


def item(title, kind="track", url=None, source="Audius"):
    return S.Item(kind, title, source=source, url=url or f"http://x/{title}")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "STATE_FILE", tmp_path / "state.json")
    return A.Store()


def reload(store):
    return A.Store()


def titles(items):
    return [i.title for i in items]


# -- playlists

def test_create_playlist_persists(store):
    assert store.create_playlist("Road trip") is True
    assert store.playlist_names() == ["Road trip"]
    assert reload(store).playlist_names() == ["Road trip"]


def test_create_playlist_rejects_blank_and_duplicate(store):
    store.create_playlist("Mix")
    assert store.create_playlist("Mix") is False
    assert store.create_playlist("  mix ") is False  # case / whitespace insensitive
    assert store.create_playlist("   ") is False
    assert store.playlist_names() == ["Mix"]


def test_add_to_playlist_appends_and_skips_duplicates(store):
    store.create_playlist("Mix")
    assert store.add_to_playlist("Mix", item("a")) is True
    assert store.add_to_playlist("Mix", item("b")) is True
    assert store.add_to_playlist("Mix", item("a")) is False
    assert titles(store.playlist_items("Mix")) == ["a", "b"]
    assert titles(reload(store).playlist_items("Mix")) == ["a", "b"]


def test_rename_playlist_keeps_items_and_sort(store):
    store.create_playlist("Old")
    store.add_to_playlist("Old", item("a"))
    store.set_sort("pl:Old", "name")
    assert store.rename_playlist("Old", "New") is True
    assert store.playlist_names() == ["New"]
    assert titles(store.playlist_items("New")) == ["a"]
    assert store.get_sort("pl:New") == "name"


def test_rename_playlist_rejects_taken_name(store):
    store.create_playlist("A")
    store.create_playlist("B")
    assert store.rename_playlist("A", "b") is False
    assert store.rename_playlist("A", "") is False
    assert store.playlist_names() == ["A", "B"]


def test_delete_playlist(store):
    store.create_playlist("A")
    store.set_sort("pl:A", "name")
    store.delete_playlist("A")
    assert store.playlist_names() == []
    assert store.get_sort("pl:A") == "custom"


def test_remove_from_playlist(store):
    store.create_playlist("Mix")
    a, b = item("a"), item("b")
    store.add_to_playlist("Mix", a)
    store.add_to_playlist("Mix", b)
    store.remove_from_playlist("Mix", a)
    assert titles(store.playlist_items("Mix")) == ["b"]


# -- reordering

def test_move_in_playlist(store):
    store.create_playlist("Mix")
    for t in "abc":
        store.add_to_playlist("Mix", item(t))
    assert store.move("pl:Mix", 0, 1) == 1
    assert titles(store.playlist_items("Mix")) == ["b", "a", "c"]
    assert store.move("pl:Mix", 2, -2) == 0
    assert titles(store.playlist_items("Mix")) == ["c", "b", "a"]
    assert titles(reload(store).playlist_items("Mix")) == ["c", "b", "a"]


def test_move_clamps_at_edges(store):
    store.create_playlist("Mix")
    for t in "ab":
        store.add_to_playlist("Mix", item(t))
    assert store.move("pl:Mix", 0, -1) == 0
    assert store.move("pl:Mix", 1, 1) == 1
    assert titles(store.playlist_items("Mix")) == ["a", "b"]


def test_move_in_favourites(store):
    for t in "abc":
        store.toggle_fav(item(t))  # newest first: c, b, a
    assert store.move("favs", 0, 2) == 2
    assert titles(store.items("favorites")) == ["b", "a", "c"]


# -- sorting

def test_sort_modes(store):
    store.create_playlist("Mix")
    store.add_to_playlist("Mix", item("beta", kind="track", source="ccMixter"))
    store.add_to_playlist("Mix", item("Alpha", kind="station", source="SomaFM"))
    store.add_to_playlist("Mix", item("gamma", kind="episode", source="Apple"))
    items = store.playlist_items("Mix")
    assert titles(A.sort_items(items, "custom")) == ["beta", "Alpha", "gamma"]
    assert titles(A.sort_items(items, "name")) == ["Alpha", "beta", "gamma"]
    assert titles(A.sort_items(items, "type")) == ["gamma", "Alpha", "beta"]  # episode, station, track
    assert titles(A.sort_items(items, "source")) == ["gamma", "beta", "Alpha"]  # Apple, ccMixter, SomaFM


def test_sort_by_date_added_is_newest_first(store, monkeypatch):
    clock = iter([100.0, 300.0, 200.0])
    monkeypatch.setattr(A.time, "time", lambda: next(clock))
    store.create_playlist("Mix")
    for t in "abc":
        store.add_to_playlist("Mix", item(t))
    assert titles(A.sort_items(store.playlist_items("Mix"), "added")) == ["b", "c", "a"]


def test_sort_does_not_change_stored_order(store):
    store.create_playlist("Mix")
    for t in "cab":
        store.add_to_playlist("Mix", item(t))
    A.sort_items(store.playlist_items("Mix"), "name")
    assert titles(store.playlist_items("Mix")) == ["c", "a", "b"]


def test_next_sort_cycles_and_persists(store):
    assert store.get_sort("favs") == "custom"
    seen = [store.next_sort("favs") for _ in range(len(A.SORT_MODES))]
    assert seen == A.SORT_MODES[1:] + A.SORT_MODES[:1]
    store.next_sort("favs")
    assert reload(store).get_sort("favs") == A.SORT_MODES[1]


# -- compatibility

def test_old_state_file_loads(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    monkeypatch.setattr(A, "STATE_FILE", path)
    old = {"volume": 50, "favorites": [item("a").to_dict(), item("b").to_dict()], "history": [], "positions": {}}
    path.write_text(json.dumps(old), "utf-8")
    s = A.Store()
    assert s.playlist_names() == []
    assert s.get_sort("favs") == "custom"
    # favourites without an "added" stamp keep their stored (newest-first) order under date sort
    assert titles(A.sort_items(s.items("favorites"), "added")) == ["a", "b"]

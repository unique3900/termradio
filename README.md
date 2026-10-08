# termradio

Radio, music and podcasts in your terminal. No browser, no API keys, no extra executables:
audio goes through the VLC you already have installed (`python-vlc` loads `libvlc.dll` from it).

## Install

```
pip install termradio
termradio
termradio --country IN   # podcast charts for India (remembered)
```

Needs Python 3.9+ and [VLC 3.x](https://www.videolan.org/vlc/) installed (the app plays audio through it).

## What's in it

| Tab | Sources | What you get |
|---|---|---|
| **1 Radio** | Radio Browser, SomaFM, a curated list | ~50,000 live stations. Browse by genre, country or language, or see trending, most played and top voted. 49 hand-checked stations (Radio Paradise, KEXP, FIP, NTS, BBC World Service, WFMU, Swiss Jazz, Nightride…) and all ~46 SomaFM channels |
| **2 Music** | Audius, Internet Archive, ccMixter | Full-length on-demand tracks. Audius has trending tracks by genre (36 genres) and playlists. Internet Archive has live concerts, Grateful Dead, netlabels, 78 RPM records, jazz and classical. ccMixter has Creative Commons tracks. Search covers all three |
| **3 Podcasts** | Apple Podcasts directory + RSS, Internet Archive | Top charts in 20 categories, search for any podcast, full episode lists. Also LibriVox audiobooks and Old Time Radio. Episodes remember where you stopped |
| **4 Favourites** | – | Anything you star with `f` or the ☆ Fav button: stations, tracks, albums, playlists, podcasts, episodes, and whole categories from the sidebar (e.g. Radio › Jazz). Sort with `o`, reorder with `shift+↑/↓` |
| **5 History** | – | The last 300 things you played |
| **6 Playlists** | – | Your own playlists. Add any row from any tab with `a` or the + Playlist button (pick a playlist or create one). Sort with `o`, reorder with `shift+↑/↓`; playback follows the order shown |

## Keys

| Key | Action |
|---|---|
| `enter` | play, or open a folder / album / podcast |
| `backspace` / `esc` | go back to the previous list (after opening something, a search or a category); while loading, cancels |
| `/` | search (in the current tab) |
| `space` | pause / resume |
| `s` | stop |
| `n` / `p` | next / previous in the list |
| `+` / `-` | volume, `m` mute |
| `[` / `]` | seek -15s / +30s (tracks and episodes) |
| `,` / `.` | seek -5s / +5s |
| mouse | click or drag the progress bar at the bottom to jump anywhere |
| `f` | add / remove favourite |
| `c` | copy a shareable ID for the selected item |
| `a` | add the selected item to a playlist (or create a new one) |
| `o` | sort favourites / a playlist: custom → name → date added → type → source |
| `shift+↑` / `shift+↓` | move the highlighted row up / down (custom sort only) |
| `delete` | remove the highlighted item from the playlist; on a playlist in the sidebar, delete the playlist |
| `r` | rename the playlist highlighted in the sidebar |
| `t` | sleep timer: 15 → 30 → 60 → 90 min → off |
| `1`–`6` | switch tab |
| `q` | quit |

**Favourites and sharing.** `f` / ☆ Fav and `c` / ⧉ Copy ID act on the highlighted row, or on the
highlighted sidebar category when the sidebar has focus. The copied ID looks like
`termradio:audius-album:eNpN…`; paste it into any tab's search box to bring that exact item up
(play it, open it or favourite it). It carries everything needed, so it works on a colleague's machine too.

Radio search can filter: `tag:jazz`, `country:IN`, `lang:hindi`, and they combine,
e.g. `country:IN tag:bollywood` or `mirchi country:IN`.

## Files

- `app.py`: the terminal UI (Textual)
- `sources.py`: every data source. All are free and need no key
- `player.py`: playback through libvlc
- If audio won't start, termradio says why and exits: VLC missing, 32-bit VLC with 64-bit Python
  (or the reverse), or VLC 4 (only VLC 3.x is supported). Installed somewhere unusual? Set
  `TERMRADIO_VLC_DIR` to the folder that contains `libvlc.dll`.
- State (favourites, history, volume, podcast positions): `~/.termradio/state.json`

## Notes

- SomaFM rejects requests that send no User-Agent (you get a 403, as with plain `curl`).
  termradio always sends one.
- ccMixter refuses hotlinks without a Referer header. The player sends the stream's own site as the Referer.
- Podcast URLs often chain 4–6 tracking redirects, which VLC won't follow, so they're resolved in Python first.
- Radio streams that drop reconnect automatically, up to 3 times in a row.

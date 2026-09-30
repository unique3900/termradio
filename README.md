# termradio

Radio, music and podcasts in your terminal. No browser, no API keys, no extra executables:
audio goes through the VLC you already have installed (`python-vlc` loads `libvlc.dll` from it).

```
python C:\Users\parashar\termradio\termradio.py
python C:\Users\parashar\termradio\termradio.py --country IN   # podcast charts for India (remembered)
```

Optional shortcut: add this line to your PowerShell profile (`notepad $PROFILE`), then just type `radio`:

```powershell
function radio { python C:\Users\parashar\termradio\termradio.py @args }
```

## What's in it

| Tab | Sources | What you get |
|---|---|---|
| **1 Radio** | Radio Browser, SomaFM, a curated list | ~50,000 live stations. Browse by genre, country or language, or see trending, most played and top voted. 49 hand-checked stations (Radio Paradise, KEXP, FIP, NTS, BBC World Service, WFMU, Swiss Jazz, Nightride…) and all ~46 SomaFM channels |
| **2 Music** | Audius, Internet Archive, ccMixter | Full-length on-demand tracks. Audius has trending tracks by genre (36 genres) and playlists. Internet Archive has live concerts, Grateful Dead, netlabels, 78 RPM records, jazz and classical. ccMixter has Creative Commons tracks. Search covers all three |
| **3 Podcasts** | Apple Podcasts directory + RSS, Internet Archive | Top charts in 20 categories, search for any podcast, full episode lists. Also LibriVox audiobooks and Old Time Radio. Episodes remember where you stopped |
| **4 Favourites** | – | Anything you star with `f`: stations, tracks, albums, podcasts, episodes |
| **5 History** | – | The last 300 things you played |

## Keys

| Key | Action |
|---|---|
| `enter` | play, or open a folder / album / podcast |
| `backspace` / `esc` | go back up |
| `/` | search (in the current tab) |
| `space` | pause / resume |
| `s` | stop |
| `n` / `p` | next / previous in the list |
| `+` / `-` | volume, `m` mute |
| `[` / `]` | seek -15s / +30s (tracks and episodes) |
| `f` | add / remove favourite |
| `t` | sleep timer: 15 → 30 → 60 → 90 min → off |
| `1`–`5` | switch tab |
| `q` | quit |

Radio search can filter: `tag:jazz`, `country:IN`, `lang:hindi`, and they combine,
e.g. `country:IN tag:bollywood` or `mirchi country:IN`.

## Files

- `termradio.py`: the terminal UI (Textual)
- `sources.py`: every data source. All are free and need no key
- `player.py`: playback through libvlc
- State (favourites, history, volume, podcast positions): `~/.termradio/state.json`

## Notes

- SomaFM rejects requests that send no User-Agent (you get a 403, as with plain `curl`).
  termradio always sends one.
- ccMixter refuses hotlinks without a Referer header. The player sends the stream's own site as the Referer.
- Podcast URLs often chain 4–6 tracking redirects, which VLC won't follow, so they're resolved in Python first.
- Radio streams that drop reconnect automatically, up to 3 times in a row.

Install / update the two dependencies:

```
python -m pip install --user -r C:\Users\parashar\termradio\requirements.txt
```

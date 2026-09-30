"""Audio playback through libvlc (uses the VLC already installed on this machine).

No browser, no extra executables: python-vlc is a plain ctypes wrapper that loads
libvlc.dll from the VLC install folder.
"""
from __future__ import annotations

import os
import sys

from sources import UA

if sys.platform == "win32":
    for d in (r"C:\Program Files\VideoLAN\VLC", r"C:\Program Files (x86)\VideoLAN\VLC"):
        if os.path.exists(os.path.join(d, "libvlc.dll")):
            os.environ.setdefault("PYTHON_VLC_MODULE_PATH", d)
            os.environ.setdefault("PYTHON_VLC_LIB_PATH", os.path.join(d, "libvlc.dll"))
            try:
                os.add_dll_directory(d)
            except (AttributeError, OSError):
                pass
            break

import vlc  # noqa: E402


class Player:
    def __init__(self, volume: int = 70):
        self.instance = vlc.Instance(
            "--quiet", "--no-video", "--intf=dummy", "--no-xlib",
            f"--http-user-agent={UA}", "--network-caching=3000",
        )
        self.mp = self.instance.media_player_new()
        self.media = None
        self._volume = volume
        self.mp.audio_set_volume(volume)

    # -- control
    def play(self, url: str, start_ms: int = 0) -> None:
        self.mp.stop()
        self.media = self.instance.media_new(url)
        self.media.add_option(f":http-user-agent={UA}")
        origin = "/".join(url.split("/")[:3]) + "/"
        self.media.add_option(f":http-referrer={origin}")  # ccMixter refuses hotlinks without it
        if start_ms > 0:
            self.media.add_option(f":start-time={start_ms / 1000:.1f}")
        self.mp.set_media(self.media)
        self.mp.play()
        self.mp.audio_set_volume(self._volume)

    def stop(self) -> None:
        self.mp.stop()

    def toggle_pause(self) -> None:
        if self.mp.get_state() in (vlc.State.Playing, vlc.State.Paused):
            self.mp.pause()

    def seek(self, delta_ms: int) -> None:
        length = self.mp.get_length()
        if length > 0 and self.mp.is_seekable():
            self.mp.set_time(max(0, min(length - 1000, self.mp.get_time() + delta_ms)))

    @property
    def volume(self) -> int:
        return self._volume

    @volume.setter
    def volume(self, v: int) -> None:
        self._volume = max(0, min(150, v))
        self.mp.audio_set_volume(self._volume)

    def toggle_mute(self) -> None:
        self.mp.audio_toggle_mute()

    # -- status
    @property
    def state(self) -> str:
        return {
            vlc.State.NothingSpecial: "idle", vlc.State.Opening: "connecting",
            vlc.State.Buffering: "buffering", vlc.State.Playing: "playing",
            vlc.State.Paused: "paused", vlc.State.Stopped: "stopped",
            vlc.State.Ended: "ended", vlc.State.Error: "error",
        }.get(self.mp.get_state(), "idle")

    @property
    def muted(self) -> bool:
        return bool(self.mp.audio_get_mute())

    @property
    def time_ms(self) -> int:
        return max(0, self.mp.get_time())

    @property
    def length_ms(self) -> int:
        return max(0, self.mp.get_length())

    def now_playing(self) -> str:
        """ICY 'StreamTitle' for radio, or the track's own tags."""
        if not self.media:
            return ""
        np = self.media.get_meta(vlc.Meta.NowPlaying)
        if np:
            return np
        artist, title = self.media.get_meta(vlc.Meta.Artist), self.media.get_meta(vlc.Meta.Title)
        if title and artist:
            return f"{artist} - {title}"
        return ""

    def close(self) -> None:
        try:
            self.mp.stop()
            self.mp.release()
            self.instance.release()
        except Exception:
            pass

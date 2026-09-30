"""Audio playback through libvlc (uses the VLC already installed on this machine).

No browser, no extra executables: python-vlc is a plain ctypes wrapper that loads
libvlc.dll from the VLC install folder.
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
from pathlib import Path

from sources import UA

PY_BITS = struct.calcsize("P") * 8
VLC_URL = "https://www.videolan.org/vlc/"


class PlayerError(RuntimeError):
    """VLC is missing or unusable; the message says what to do about it."""


def dll_bits(path: Path) -> int | None:
    """32 or 64, read from the DLL's PE header (so we never ask ctypes to load the wrong one)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        pe = struct.unpack_from("<I", head, 0x3C)[0]
        machine = struct.unpack_from("<H", head, pe + 4)[0]
    except (OSError, struct.error):
        return None
    return {0x14C: 32, 0x8664: 64, 0xAA64: 64}.get(machine)


def windows_vlc_dirs() -> list[Path]:
    """Every place VLC is commonly installed: installer registry keys (32- and 64-bit views),
    Program Files, per-user installs and scoop. TERMRADIO_VLC_DIR overrides them all."""
    dirs: list[str] = [os.environ.get("TERMRADIO_VLC_DIR", "")]
    try:
        import winreg as w
        for root in (w.HKEY_LOCAL_MACHINE, w.HKEY_CURRENT_USER):
            for view in (w.KEY_WOW64_64KEY, w.KEY_WOW64_32KEY):
                try:
                    with w.OpenKey(root, r"Software\VideoLAN\VLC", 0, w.KEY_READ | view) as k:
                        dirs.append(w.QueryValueEx(k, "InstallDir")[0])
                except OSError:
                    pass
    except ImportError:
        pass
    for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        dirs.append(os.path.join(os.environ.get(var, ""), "VideoLAN", "VLC"))
    dirs.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "VideoLAN", "VLC"))
    dirs.append(str(Path.home() / "scoop" / "apps" / "vlc" / "current"))
    seen, out = set(), []
    for d in dirs:
        if d and os.path.isabs(d) and (p := Path(d)) not in seen and (p / "libvlc.dll").is_file():
            seen.add(p)
            out.append(p)
    return out


def setup_windows() -> None:
    """Point python-vlc at a libvlc.dll this Python can actually load. python-vlc's own lookup can
    pick a 32-bit VLC for a 64-bit Python (or a stray libvlc.dll on PATH), and when a DLL fails to
    load it calls sys.exit(1) without a word, so the app just vanished on some machines."""
    if os.environ.get("PYTHON_VLC_LIB_PATH"):  # user chose one explicitly; just make sure it loads
        try:
            ctypes.CDLL(os.environ["PYTHON_VLC_LIB_PATH"])
            return
        except OSError as e:
            raise PlayerError(f"PYTHON_VLC_LIB_PATH={os.environ['PYTHON_VLC_LIB_PATH']} can't be loaded: {e}")
    found = windows_vlc_dirs()
    usable = [d for d in found if dll_bits(d / "libvlc.dll") in (PY_BITS, None)]
    if not usable:
        if found:
            other = 64 if PY_BITS == 32 else 32
            raise PlayerError(
                f"Found {other}-bit VLC in {found[0]}, but this Python is {PY_BITS}-bit and can't load it.\n"
                f"Install the {PY_BITS}-bit VLC from {VLC_URL},\n"
                f"or run termradio with a {other}-bit Python.")
        raise PlayerError(f"VLC isn't installed (no libvlc.dll found). Install VLC 3 from {VLC_URL}\n"
                          "or set TERMRADIO_VLC_DIR to the folder that contains libvlc.dll.")
    d = usable[0]
    try:
        os.add_dll_directory(str(d))  # libvlc.dll needs libvlccore.dll from the same folder
    except (AttributeError, OSError):
        pass
    os.environ["PYTHON_VLC_LIB_PATH"] = str(d / "libvlc.dll")
    os.environ["PYTHON_VLC_MODULE_PATH"] = str(d)
    try:
        ctypes.CDLL(os.environ["PYTHON_VLC_LIB_PATH"])
    except OSError as e:
        raise PlayerError(f"VLC in {d} is installed but can't be loaded ({e}).\n"
                          f"Reinstalling VLC 3 from {VLC_URL} usually fixes this.")


def load_vlc():
    if sys.platform == "win32":
        setup_windows()
    try:
        import vlc
    except ImportError:
        raise PlayerError("The python-vlc package is missing: pip install -r requirements.txt") from None
    except (OSError, SystemExit) as e:  # python-vlc exits instead of raising when libvlc won't load
        raise PlayerError(f"VLC couldn't be loaded ({e}). Install VLC 3 from {VLC_URL}") from None
    if getattr(vlc, "dll", None) is None:
        raise PlayerError(f"VLC isn't installed or couldn't be found. Install VLC 3 from {VLC_URL}")
    try:
        version = vlc.libvlc_get_version().decode(errors="replace")
    except Exception:
        version = "unknown"
    major = int(version.split(".")[0]) if version[:1].isdigit() else 0
    if major >= 4:
        raise PlayerError(f"VLC {version} is installed, but termradio's python-vlc bindings only support\n"
                          f"VLC 3.x (VLC 4 changed its API). Install the stable VLC 3 from {VLC_URL}")
    if major and major < 3:
        raise PlayerError(f"VLC {version} is too old. Install VLC 3 from {VLC_URL}")
    return vlc


vlc = load_vlc_error = None
try:
    vlc = load_vlc()
except PlayerError as e:
    load_vlc_error = e


class Player:
    def __init__(self, volume: int = 70):
        if vlc is None:
            raise load_vlc_error  # type: ignore[misc]
        opts = ["--quiet", "--no-video", "--intf=dummy", f"--http-user-agent={UA}", "--network-caching=3000"]
        if sys.platform.startswith("linux"):
            opts.append("--no-xlib")
        # libvlc refuses to start on an option its build doesn't know, so fall back to none at all.
        self.instance = vlc.Instance(*opts) or vlc.Instance()
        if self.instance is None:
            raise PlayerError(f"VLC {vlc.libvlc_get_version().decode(errors='replace')} failed to start "
                              f"(broken plugins?). Reinstalling VLC 3 from {VLC_URL} usually fixes this.")
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
        self.seek_to(self.mp.get_time() + delta_ms)

    def seek_to(self, ms: int) -> None:
        length = self.mp.get_length()
        if length > 0 and self.mp.is_seekable():
            self.mp.set_time(max(0, min(length - 1000, ms)))

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

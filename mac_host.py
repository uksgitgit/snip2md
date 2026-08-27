"""macOS screen bounds, capture, single-instance lock, and Carbon hotkeys.

Hotkeys use RegisterEventHotKey (the same idea as Win32 RegisterHotKey):
one combo is registered with the system. This is not a keylogger and does
not use CGEventTap / Accessibility.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import fcntl
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image

from providers import ProviderError

LOCK_PATH = Path.home() / ".snip2md" / "snip2md.lock"

# Carbon EventModifiers / RegisterEventHotKey masks
_CMD_KEY = 256
_SHIFT_KEY = 512
_OPTION_KEY = 2048
_CONTROL_KEY = 4096

_K_EVENT_CLASS_KEYBOARD = 0x6B657962  # 'keyb'
_K_EVENT_HOTKEY_PRESSED = 5
_HOTKEY_SIGNATURE = 0x73326D64  # 's2md'
_HOTKEY_ID = 1

# ANSI virtual key codes (HIToolbox Events.h). Letters are physical keys.
_MAC_KEYCODES = {
    "a": 0x00,
    "s": 0x01,
    "d": 0x02,
    "f": 0x03,
    "h": 0x04,
    "g": 0x05,
    "z": 0x06,
    "x": 0x07,
    "c": 0x08,
    "v": 0x09,
    "b": 0x0B,
    "q": 0x0C,
    "w": 0x0D,
    "e": 0x0E,
    "r": 0x0F,
    "y": 0x10,
    "t": 0x11,
    "1": 0x12,
    "2": 0x13,
    "3": 0x14,
    "4": 0x15,
    "6": 0x16,
    "5": 0x17,
    "9": 0x19,
    "7": 0x1A,
    "8": 0x1C,
    "0": 0x1D,
    "o": 0x1F,
    "u": 0x20,
    "i": 0x22,
    "p": 0x23,
    "l": 0x25,
    "j": 0x26,
    "k": 0x28,
    "n": 0x2D,
    "m": 0x2E,
    "f1": 0x7A,
    "f2": 0x78,
    "f3": 0x63,
    "f4": 0x76,
    "f5": 0x60,
    "f6": 0x61,
    "f7": 0x62,
    "f8": 0x64,
    "f9": 0x65,
    "f10": 0x6D,
    "f11": 0x67,
    "f12": 0x6F,
}

_SCREEN_PERMISSION = (
    "macOS blocked the screenshot. Allow Screen Recording for Snip2MD "
    "(or Python / Terminal) in System Settings → Privacy & Security, "
    "then restart Snip2MD."
)

_lock_fp = None
_carbon = None
_core_foundation = None
_core_graphics = None
_hotkey_handler_c = None
_active_listener = None


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_HotKeyHandler = ctypes.CFUNCTYPE(
    ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
)


def _load_framework(name: str) -> ctypes.CDLL:
    path = ctypes.util.find_library(name)
    if path:
        return ctypes.CDLL(path)
    candidates = (
        f"/System/Library/Frameworks/{name}.framework/{name}",
        f"/System/Library/Frameworks/{name}.framework/Versions/A/{name}",
        (
            "/System/Library/Frameworks/Carbon.framework/Frameworks/"
            f"{name}.framework/{name}"
        ),
        (
            "/System/Library/Frameworks/Carbon.framework/Versions/A/Frameworks/"
            f"{name}.framework/Versions/A/{name}"
        ),
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            return ctypes.CDLL(candidate)
    raise ProviderError(f"Could not load {name}.framework")


def _hi_toolbox() -> ctypes.CDLL:
    global _carbon
    if _carbon is None:
        try:
            _carbon = _load_framework("Carbon")
        except ProviderError:
            _carbon = _load_framework("HIToolbox")
        _carbon.GetApplicationEventTarget.argtypes = []
        _carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        _carbon.InstallEventHandler.argtypes = [
            ctypes.c_void_p,
            _HotKeyHandler,
            ctypes.c_uint32,
            ctypes.POINTER(_EventTypeSpec),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        _carbon.InstallEventHandler.restype = ctypes.c_int32
        _carbon.RemoveEventHandler.argtypes = [ctypes.c_void_p]
        _carbon.RemoveEventHandler.restype = ctypes.c_int32
        _carbon.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            _EventHotKeyID,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        _carbon.RegisterEventHotKey.restype = ctypes.c_int32
        _carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        _carbon.UnregisterEventHotKey.restype = ctypes.c_int32
        _carbon.GetCurrentKeyModifiers.argtypes = []
        _carbon.GetCurrentKeyModifiers.restype = ctypes.c_uint32
    return _carbon


def _cf() -> ctypes.CDLL:
    global _core_foundation
    if _core_foundation is None:
        _core_foundation = _load_framework("CoreFoundation")
        _core_foundation.CFRunLoopRun.argtypes = []
        _core_foundation.CFRunLoopRun.restype = None
        _core_foundation.CFRunLoopGetCurrent.argtypes = []
        _core_foundation.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        _core_foundation.CFRunLoopStop.argtypes = [ctypes.c_void_p]
        _core_foundation.CFRunLoopStop.restype = None
    return _core_foundation


def _cg() -> ctypes.CDLL:
    global _core_graphics
    if _core_graphics is None:
        _core_graphics = _load_framework("CoreGraphics")
        _core_graphics.CGGetActiveDisplayList.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        _core_graphics.CGGetActiveDisplayList.restype = ctypes.c_int32
        _core_graphics.CGDisplayBounds.argtypes = [ctypes.c_uint32]
        _core_graphics.CGDisplayBounds.restype = _CGRect
        _core_graphics.CGMainDisplayID.argtypes = []
        _core_graphics.CGMainDisplayID.restype = ctypes.c_uint32
    return _core_graphics


def virtual_screen_bounds() -> tuple[int, int, int, int]:
    """Union of display bounds in global top-left coordinates (points)."""
    try:
        graphics = _cg()
        max_displays = 16
        displays = (ctypes.c_uint32 * max_displays)()
        count = ctypes.c_uint32(0)
        err = graphics.CGGetActiveDisplayList(
            max_displays, displays, ctypes.byref(count)
        )
        ids: list[int]
        if err == 0 and count.value:
            ids = [int(displays[i]) for i in range(count.value)]
        else:
            ids = [int(graphics.CGMainDisplayID())]
        left = top = 10**9
        right = bottom = -(10**9)
        for display in ids:
            rect = graphics.CGDisplayBounds(display)
            x = float(rect.origin.x)
            y = float(rect.origin.y)
            width = float(rect.size.width)
            height = float(rect.size.height)
            left = min(left, x)
            top = min(top, y)
            right = max(right, x + width)
            bottom = max(bottom, y + height)
        if right <= left or bottom <= top:
            raise RuntimeError("empty display bounds")
        return int(left), int(top), int(right), int(bottom)
    except Exception:
        return _tk_main_bounds()


def _tk_main_bounds() -> tuple[int, int, int, int]:
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        width = int(root.winfo_screenwidth())
        height = int(root.winfo_screenheight())
    finally:
        root.destroy()
    return 0, 0, width, height


def grab_region(bbox: tuple[int, int, int, int]) -> Image.Image:
    """Capture a rectangle. Coordinates are global points (Tk / CGDisplayBounds)."""
    time.sleep(0.08)
    x1, y1, x2, y2 = (int(v) for v in bbox)
    width = max(x2 - x1, 1)
    height = max(y2 - y1, 1)
    fd, path = tempfile.mkstemp(suffix=".png", prefix="snip2md-")
    os.close(fd)
    os.chmod(path, 0o600)
    try:
        proc = subprocess.run(
            [
                "screencapture",
                "-x",
                "-t",
                "png",
                "-R",
                f"{x1},{y1},{width},{height}",
                path,
            ],
            check=False,
            capture_output=True,
            timeout=20,
        )
        if proc.returncode != 0 or not os.path.isfile(path) or os.path.getsize(path) < 32:
            raise ProviderError(_SCREEN_PERMISSION)
        image = Image.open(path)
        image.load()
        rgb = image.convert("RGB")
        image.close()
        if rgb.size[0] < 2 or rgb.size[1] < 2:
            raise ProviderError(_SCREEN_PERMISSION)
        return rgb
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(_SCREEN_PERMISSION) from exc
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def claim_single_instance() -> bool:
    """True if this process owns ~/.snip2md/snip2md.lock."""
    global _lock_fp
    LOCK_PATH.parent.mkdir(mode=0o700, exist_ok=True)
    handle = open(LOCK_PATH, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return False
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _lock_fp = handle
    return True


def focus_existing_window() -> None:
    try:
        raw = LOCK_PATH.read_text(encoding="utf-8").strip().split()[0]
        pid = int(raw)
    except (OSError, ValueError, IndexError):
        return
    if pid <= 1:
        return
    script = (
        'tell application "System Events" to set frontmost of '
        f"(first process whose unix id is {pid}) to true"
    )
    subprocess.run(
        ["osascript", "-e", script],
        check=False,
        capture_output=True,
        timeout=8,
    )


def _parts_key(spec: str) -> tuple[list[str], str]:
    mods: list[str] = []
    key: str | None = None
    for part in spec.lower().replace(" ", "").split("+"):
        if not part:
            continue
        if part in ("ctrl", "control"):
            mods.append("ctrl")
        elif part in ("alt", "option"):
            mods.append("alt")
        elif part == "shift":
            mods.append("shift")
        elif part in ("win", "windows", "cmd", "command", "super"):
            mods.append("win")
        else:
            key = part
    if not key:
        raise ProviderError("Hotkey needs a letter, digit, or F-key.")
    ordered = [name for name in ("ctrl", "alt", "shift", "win") if name in mods]
    return ordered, key


def carbon_hotkey(parts: list[str], key: str) -> tuple[int, int]:
    code = _MAC_KEYCODES.get(key)
    if code is None:
        raise ProviderError(
            f"Unsupported hotkey key '{key}'. Use a letter, digit, or F-key."
        )
    mods = 0
    if "ctrl" in parts:
        mods |= _CONTROL_KEY
    if "alt" in parts:
        mods |= _OPTION_KEY
    if "shift" in parts:
        mods |= _SHIFT_KEY
    if "win" in parts:
        mods |= _CMD_KEY
    return mods, code


def hotkey_modifiers_held() -> list[str]:
    """Physical modifiers from Carbon — not Tk Aqua state bits."""
    try:
        value = int(_hi_toolbox().GetCurrentKeyModifiers())
    except Exception:
        return []
    parts: list[str] = []
    if value & _CONTROL_KEY:
        parts.append("ctrl")
    if value & _OPTION_KEY:
        parts.append("alt")
    if value & _SHIFT_KEY:
        parts.append("shift")
    if value & _CMD_KEY:
        parts.append("win")
    return parts


@_HotKeyHandler
def _on_hotkey(_next_handler, _event, _user_data):
    listener = _active_listener
    if listener is not None:
        listener.fired.set()
    return 0


class HotkeyListener:
    """Carbon RegisterEventHotKey on the Cocoa/Tk run loop. Same Event API as Windows."""

    def __init__(self) -> None:
        global _hotkey_handler_c, _active_listener
        self.fired = threading.Event()
        self._hot_ref = ctypes.c_void_p()
        self._handler_ref = ctypes.c_void_p()
        self._installed = False
        _hotkey_handler_c = _on_hotkey
        _active_listener = self
        self._install_handler()

    def _install_handler(self) -> None:
        if self._installed:
            return
        carbon = _hi_toolbox()
        target = carbon.GetApplicationEventTarget()
        if not target:
            raise ProviderError("Could not start the shortcut listener.")
        spec = _EventTypeSpec(
            eventClass=_K_EVENT_CLASS_KEYBOARD,
            eventKind=_K_EVENT_HOTKEY_PRESSED,
        )
        err = carbon.InstallEventHandler(
            target,
            _on_hotkey,
            1,
            ctypes.byref(spec),
            None,
            ctypes.byref(self._handler_ref),
        )
        if err != 0:
            raise ProviderError("Could not start the shortcut listener.")
        self._installed = True

    def register(self, spec: str) -> bool:
        self.unregister()
        parts, key = _parts_key(spec)
        mods, code = carbon_hotkey(parts, key)
        carbon = _hi_toolbox()
        target = carbon.GetApplicationEventTarget()
        hot_id = _EventHotKeyID(signature=_HOTKEY_SIGNATURE, id=_HOTKEY_ID)
        ref = ctypes.c_void_p()
        err = carbon.RegisterEventHotKey(
            code, mods, hot_id, target, 0, ctypes.byref(ref)
        )
        if err != 0 or not ref:
            return False
        self._hot_ref = ref
        return True

    def unregister(self) -> None:
        if self._hot_ref:
            try:
                _hi_toolbox().UnregisterEventHotKey(self._hot_ref)
            except Exception:
                pass
            self._hot_ref = ctypes.c_void_p()

    def close(self) -> None:
        global _active_listener
        self.unregister()
        if self._handler_ref:
            try:
                _hi_toolbox().RemoveEventHandler(self._handler_ref)
            except Exception:
                pass
            self._handler_ref = ctypes.c_void_p()
            self._installed = False
        if _active_listener is self:
            _active_listener = None


def listen_hotkey(spec: str, callback) -> None:
    """Register one global combo via Carbon — not a keylogger hook."""
    listener = HotkeyListener()
    if not listener.register(spec):
        raise ProviderError(
            f"Could not register {spec}. It may already be in use. "
            "Set SNIP2MD_HOTKEY to another combo, e.g. ctrl+shift+m"
        )
    stop = threading.Event()

    def pump() -> None:
        while not stop.is_set():
            if listener.fired.wait(timeout=0.05):
                listener.fired.clear()
                callback()

    worker = threading.Thread(target=pump, daemon=True, name="snip2md-hotkey-cb")
    worker.start()
    core = _cf()
    try:
        core.CFRunLoopRun()
    finally:
        stop.set()
        listener.close()
        try:
            loop = core.CFRunLoopGetCurrent()
            if loop:
                core.CFRunLoopStop(loop)
        except Exception:
            pass

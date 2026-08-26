"""
snip2md — snip a screen region like Klippeværktøj, get structured Markdown
on your clipboard.

Runs locally. Uses the Claude subscription you already sign in with
(`claude auth login`). No API keys.

    python snip2md.py
    or double-click "Start Snip2MD.bat"

The window opens. Change the shortcut there, or press the saved combo.
"""

from __future__ import annotations

import argparse
import ctypes
import io
import os
import queue
import sys
import threading
import time
from ctypes import wintypes

import pyperclip
from PIL import Image, ImageGrab
from ocr_local import image_to_markdown_ocr

from providers import (
    ProviderError,
    active_provider_name,
    ai_polish_enabled,
    configured_hotkey,
    default_model,
    image_to_markdown,
    public_error_message,
    start_cursor_login,
    subscription_label,
)

MAX_SIDE = 1280
MAX_BYTES = 1_200_000
MUTEX_NAME = "Local\\Snip2MD.app"
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
    wintypes.UINT,
]
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.GetLastError.restype = wintypes.DWORD
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.MsgWaitForMultipleObjects.argtypes = [
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.DWORD,
    wintypes.DWORD,
]
user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD

VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C
HOTKEY_ID = 1
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_USER = 0x0400
QS_ALLINPUT = 0x04FF
# Tk KeyPress.state on Windows (tkWinX.c GetState):
#   0x0001 Shift, 0x0004 Control, 0x20000 Alt (ALT_MASK).
#   0x0008 is Mod1Mask = NumLock, not Alt.
#   0x40000 is EXTENDED_MASK (right Ctrl, arrows, …), not Win.

_snip_lock = threading.Lock()
_mutex_handle = None
_FKEYS = {f"f{index}": 0x70 + index - 1 for index in range(1, 13)}
_MOD_KEYSYMS = {
    "Shift_L",
    "Shift_R",
    "Control_L",
    "Control_R",
    "Alt_L",
    "Alt_R",
    "Meta_L",
    "Meta_R",
    "Win_L",
    "Win_R",
    "Super_L",
    "Super_R",
    "Caps_Lock",
}


def virtual_screen_bounds():
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return x, y, x + w, y + h


class SnipOverlay:
    """Fullscreen dimmed overlay you drag a rectangle across, like the
    Windows Snipping Tool's region-select mode."""

    def __init__(self, master=None):
        import tkinter as tk

        self.result = None
        self.start = None
        self._owns_loop = master is None

        vx1, vy1, vx2, vy2 = virtual_screen_bounds()
        self.vx1, self.vy1 = vx1, vy1

        self.root = tk.Tk() if master is None else tk.Toplevel(master)
        self.root.geometry(f"{vx2 - vx1}x{vy2 - vy1}+{vx1}+{vy1}")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.25)
        self.root.configure(bg="black")
        self.root.config(cursor="crosshair")

        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.rect_id = None

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.root.bind("<Escape>", self.on_cancel)
        self.root.focus_force()

    def on_press(self, event):
        self.start = (event.x, event.y)
        self.rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#4da3ff", width=2
        )

    def on_drag(self, event):
        if self.rect_id is not None:
            x0, y0 = self.start
            self.canvas.coords(self.rect_id, x0, y0, event.x, event.y)

    def on_release(self, event):
        x0, y0 = self.start
        x1, y1 = event.x, event.y
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        if right - left > 3 and bottom - top > 3:
            self.result = (
                left + self.vx1,
                top + self.vy1,
                right + self.vx1,
                bottom + self.vy1,
            )
        self.root.destroy()

    def on_cancel(self, _event=None):
        self.result = None
        self.root.destroy()

    def run(self):
        if self._owns_loop:
            self.root.mainloop()
        else:
            self.root.wait_window()
        return self.result


def encode_screenshot(pil_image: Image.Image) -> tuple[bytes, str, tuple[int, int]]:
    rgb = pil_image.convert("RGB")
    if max(rgb.size) > MAX_SIDE:
        rgb.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    jpeg = io.BytesIO()
    rgb.save(jpeg, format="JPEG", quality=80)
    data = jpeg.getvalue()
    mime = "image/jpeg"
    if len(data) > MAX_BYTES:
        jpeg = io.BytesIO()
        rgb.save(jpeg, format="JPEG", quality=65)
        data = jpeg.getvalue()
        if len(data) > MAX_BYTES:
            raise ProviderError(
                "That region is too large to send. Snip a smaller area."
            )
    return data, mime, rgb.size


def do_snip():
    if not _snip_lock.acquire(blocking=False):
        print("Already capturing — finish or cancel the current snip first.")
        return

    try:
        overlay = SnipOverlay()
        bbox = overlay.run()
        if bbox is None:
            print("Cancelled.")
            return

        print("Captured — reading text locally...")
        img = ImageGrab.grab(bbox=bbox, all_screens=True)
        markdown = image_to_markdown_ocr(img)
        pyperclip.copy(markdown)
        preview = markdown[:80].replace("\n", " ")
        print(f"OCR copied — {len(markdown)} chars. Preview: {preview}...")
        if not ai_polish_enabled():
            return
        try:
            active_provider_name()
        except ProviderError:
            return
        print(f"Structuring via {subscription_label()}...")
        payload, mime, size = encode_screenshot(img)
        markdown = image_to_markdown(payload, mime, size, ocr_markdown=markdown)
    except ProviderError as exc:
        print(f"Error: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {public_error_message(exc)}")
        return
    finally:
        _snip_lock.release()

    if not markdown:
        print("Nothing came back — try snipping a bit more of the section.")
        return

    pyperclip.copy(markdown)
    preview = markdown[:80].replace("\n", " ")
    print(f"Done — {len(markdown)} chars copied to clipboard. Preview: {preview}...")


def claim_single_instance() -> bool:
    """True if this process owns the Snip2MD mutex."""
    global _mutex_handle
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return True
    _mutex_handle = handle
    return kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def focus_existing_window() -> None:
    hwnd = user32.FindWindowW(None, "Snip2MD")
    if not hwnd:
        return
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


def format_hotkey(spec: str) -> str:
    return spec.replace("+", " + ").upper()


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Map 'ctrl+alt+m' to RegisterHotKey modifiers and a virtual-key code."""
    mods = 0
    vk: int | None = None
    for part in spec.lower().replace(" ", "").split("+"):
        if part in ("ctrl", "control"):
            mods |= 0x0002
        elif part == "alt":
            mods |= 0x0001
        elif part == "shift":
            mods |= 0x0004
        elif part in ("win", "windows"):
            mods |= 0x0008
        elif part in _FKEYS:
            vk = _FKEYS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            raise ProviderError(
                f"Unsupported hotkey part '{part}'. Use e.g. ctrl+alt+m"
            )
    if vk is None:
        raise ProviderError("Hotkey needs a letter, digit, or F-key.")
    if not (mods & (0x0001 | 0x0002 | 0x0008)):
        raise ProviderError("Shortcut needs Ctrl, Alt, or Win.")
    return mods | 0x4000, vk  # MOD_NOREPEAT


def _key_held(vk: int) -> bool:
    # High bit means down. Check "< 0" — Python "& 0x8000" is wrong for some
    # signed GetKeyState returns (e.g. -128 / 0xFF80).
    return user32.GetKeyState(vk) < 0 or user32.GetAsyncKeyState(vk) < 0


class HotkeyListener(threading.Thread):
    """RegisterHotKey on a private thread so Tk never sees WM_HOTKEY.

    A Python WndProc dispatched from Tcl's DispatchMessage crashes with
    PyEval_RestoreThread (GIL released / thread state NULL). This thread
    only sets an Event; the Tk loop polls it and starts the snip.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True, name="snip2md-hotkey")
        self.fired = threading.Event()
        self._cmds: queue.Queue = queue.Queue()
        self._thread_id = 0
        self._ready = threading.Event()
        self._alive = True
        self.start()
        if not self._ready.wait(timeout=3):
            raise ProviderError("Could not start the shortcut listener.")

    def run(self) -> None:
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._thread_id = int(kernel32.GetCurrentThreadId())
        self._ready.set()
        try:
            while self._alive:
                self._drain_cmds()
                user32.MsgWaitForMultipleObjects(0, None, False, 50, QS_ALLINPUT)
                self._drain_cmds()
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    if msg.message == WM_QUIT:
                        self._alive = False
                        break
                    if msg.message == WM_HOTKEY:
                        self.fired.set()
                    elif msg.message != WM_USER:
                        user32.TranslateMessage(ctypes.byref(msg))
                        user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def _drain_cmds(self) -> None:
        while True:
            try:
                action, payload, result, done = self._cmds.get_nowait()
            except queue.Empty:
                return
            ok = False
            try:
                if action == "register":
                    user32.UnregisterHotKey(None, HOTKEY_ID)
                    mods, vk = parse_hotkey(payload)
                    ok = bool(user32.RegisterHotKey(None, HOTKEY_ID, mods, vk))
                elif action == "unregister":
                    user32.UnregisterHotKey(None, HOTKEY_ID)
                    ok = True
                elif action == "quit":
                    self._alive = False
                    ok = True
            except Exception:
                ok = False
            result.append(ok)
            done.set()

    def _request(self, action: str, payload=None, timeout: float = 2.0) -> bool:
        if not self.is_alive():
            return False
        done = threading.Event()
        result: list[bool] = []
        self._cmds.put((action, payload, result, done))
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_USER, 0, 0)
        if not done.wait(timeout):
            return False
        return bool(result and result[0])

    def register(self, spec: str) -> bool:
        return self._request("register", spec)

    def unregister(self) -> None:
        self._request("unregister")

    def close(self) -> None:
        self._request("quit", timeout=1.0)
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self.join(timeout=2.0)


def hotkey_modifiers_held() -> list[str]:
    """Physical modifiers from Windows — not Tk's NumLock-as-Mod1 bitmask."""
    parts: list[str] = []
    if _key_held(VK_CONTROL):
        parts.append("ctrl")
    if _key_held(VK_MENU):
        parts.append("alt")
    if _key_held(VK_SHIFT):
        parts.append("shift")
    if _key_held(VK_LWIN) or _key_held(VK_RWIN):
        parts.append("win")
    return parts


def hotkey_from_event(event) -> str | None:
    """Build a shortcut spec from a Tk KeyPress, or None while modifiers only."""
    keysym = str(getattr(event, "keysym", "") or "")
    if keysym in _MOD_KEYSYMS:
        return None
    parts = hotkey_modifiers_held()
    key = None
    if len(keysym) == 1 and keysym.isalnum():
        key = keysym.lower()
    elif keysym[:1] == "F" and keysym[1:].isdigit():
        number = int(keysym[1:])
        if 1 <= number <= 12:
            key = f"f{number}"
    if key is None:
        return None
    if "ctrl" not in parts and "alt" not in parts and "win" not in parts:
        return None
    ordered = [name for name in ("ctrl", "alt", "shift", "win") if name in parts]
    return "+".join([*ordered, key])


def listen_hotkey(spec: str, callback) -> None:
    """Register one global combo via RegisterHotKey — not a keylogger hook."""
    mods, vk = parse_hotkey(spec)
    if not user32.RegisterHotKey(None, 1, mods, vk):
        raise ProviderError(
            f"Could not register {spec}. It may already be in use. "
            "Set SNIP2MD_HOTKEY to another combo, e.g. ctrl+shift+m"
        )
    msg = wintypes.MSG()
    try:
        while True:
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0x0001):
                if msg.message == 0x0312:  # WM_HOTKEY
                    callback()
                else:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.05)
    finally:
        user32.UnregisterHotKey(None, 1)


def on_hotkey():
    threading.Thread(target=do_snip, daemon=True).start()


def print_status() -> None:
    try:
        provider = active_provider_name()
    except ProviderError as exc:
        print("signed in: no")
        print(f"help: {exc}")
        return
    print("signed in: yes")
    print(f"via: {subscription_label()}")
    print(f"model: {default_model(provider)}")


def cmd_login() -> int:
    print("Opening Cursor sign-in in the browser (no API key)...")
    try:
        start_cursor_login()
    except ProviderError as exc:
        print(f"error: {exc}")
        return 1
    print("Finish sign-in in the window that opened, then run python snip2md.py")
    return 0


def cmd_run_cli() -> int:
    if sys.platform != "win32":
        print("error: snip2md is Windows-only (screen coordinates use Win32 APIs)")
        return 1

    try:
        active_provider_name()
    except ProviderError as exc:
        print(f"error: {exc}")
        return 1

    print_status()
    combo = configured_hotkey()
    try:
        print(f"snip2md ready — press {combo} to snip, Ctrl+C here to quit.")
        listen_hotkey(combo, on_hotkey)
    except KeyboardInterrupt:
        print("\nBye.")
        return 0
    except ProviderError as exc:
        print(f"error: {exc}")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snip2md",
        description="Snip any screen region to Markdown using your Claude subscription.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("login", help="Sign in with Claude Code (same as claude auth login).")
    sub.add_parser("status", help="Show whether Claude Code is signed in.")
    sub.add_parser("cli", help="Hotkey-only mode in the terminal, no window.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "command", None)
    if command == "login":
        return cmd_login()
    if command == "status":
        print_status()
        return 0
    if command == "cli":
        return cmd_run_cli()
    from ui import run_app

    return run_app()


if __name__ == "__main__":
    sys.exit(main())

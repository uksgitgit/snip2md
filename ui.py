"""Desktop window for snip2md — sign in, snip, copy. No terminal required."""

from __future__ import annotations

import ctypes
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

import pyperclip
from ocr_local import image_to_markdown_ocr, warmup_ocr

from providers import (
    ProviderError,
    active_provider_name,
    ai_polish_enabled,
    close_cursor_runtime,
    configured_hotkey,
    has_claude_subscription,
    has_cursor_subscription,
    image_to_markdown,
    preferred_provider,
    public_error_message,
    reap_snip2md_bridges,
    register_runtime_shutdown,
    set_ai_polish,
    set_hotkey,
    set_preferred_provider,
    start_claude_login,
    start_cursor_login,
    subscription_label,
    warmup_cursor,
)
from snip2md import (
    HotkeyListener,
    SnipOverlay,
    claim_single_instance,
    encode_screenshot,
    focus_existing_window,
    format_hotkey,
    grab_region,
    hotkey_capture_hint,
    hotkey_from_event,
    parse_hotkey,
    paste_shortcut,
)

BG = "#F3F4F6"
CARD = "#FFFFFF"
CARD_ACTIVE = "#4A90E2"
TEXT = "#1C1C1E"
MUTED = "#8E8E93"
ACCENT = "#4A90E2"
ACCENT_HOVER = "#3B7FD1"
LINE = "#C5DCF5"
OK = "#4A90E2"
WARN = "#E85D5D"
FOCUS = "#7EB3EA"
WHITE = "#FFFFFF"


class Card(tk.Frame):
    """White (or blue) padded card on the gray canvas."""

    def __init__(self, master, *, active: bool = False, **kw):
        fill = CARD_ACTIVE if active else CARD
        super().__init__(master, bg=fill, padx=18, pady=16, highlightthickness=0, **kw)
        self._fill = fill

    def set_active(self, on: bool) -> None:
        self._fill = CARD_ACTIVE if on else CARD
        self.configure(bg=self._fill)
        for child in self.winfo_children():
            try:
                child.configure(bg=self._fill)
            except tk.TclError:
                pass


class Snip2MdApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Snip2MD")
        self.root.configure(bg=BG)
        self.root.geometry("400x760+80+60")
        self.root.minsize(360, 680)
        if sys.platform == "win32":
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "snip2md.app"
                )
            except Exception:
                pass
        elif sys.platform == "darwin":
            try:
                self.root.createcommand("tk::mac::Quit", self._quit)
            except tk.TclError:
                pass

        self.busy = False
        self.last_markdown = ""
        self.hotkey_ok = False
        self._status_job = None
        self._snip_id = 0
        self._copied_job = None
        self._card_active = False
        self._capturing_hotkey = False
        self._hotkey_spec = configured_hotkey()
        self._hotkey = HotkeyListener()

        self._fonts()
        self._build()
        self._bind_keys()
        self.refresh_status()
        self.root.after(200, self._register_hotkey)
        self.root.after(240, self._poll_hotkey)
        self.root.after(400, self._warmup)
        self.root.after(4000, self._poll_login)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)

    def _fonts(self) -> None:
        family = "Helvetica Neue" if sys.platform == "darwin" else "Segoe UI"
        mono = "Menlo" if sys.platform == "darwin" else "Cascadia Mono"
        self.font_kicker = tkfont.Font(family=family, size=11)
        self.font_title = tkfont.Font(family=family, size=32, weight="bold")
        self.font_card = tkfont.Font(family=family, size=15, weight="bold")
        self.font_body = tkfont.Font(family=family, size=11)
        self.font_small = tkfont.Font(family=family, size=10)
        self.font_button = tkfont.Font(family=family, size=12, weight="bold")
        self.font_fab = tkfont.Font(family=family, size=22, weight="bold")
        self.font_mono = tkfont.Font(family=mono, size=10)

    def _build(self) -> None:
        shell = tk.Frame(self.root, bg=BG, padx=24, pady=22)
        shell.pack(fill="both", expand=True)

        hk_row = tk.Frame(shell, bg=BG)
        hk_row.pack(anchor="w", fill="x")
        self.hotkey_var = tk.StringVar(value=format_hotkey(self._hotkey_spec))
        tk.Label(
            hk_row,
            textvariable=self.hotkey_var,
            font=self.font_kicker,
            fg=MUTED,
            bg=BG,
        ).pack(side="left")
        self.hotkey_btn = tk.Button(
            hk_row,
            text="Change shortcut",
            font=self.font_small,
            command=self._toggle_hotkey_capture,
            bg=CARD,
            fg=ACCENT,
            activebackground=WHITE,
            activeforeground=ACCENT_HOVER,
            relief="flat",
            cursor="hand2",
            highlightthickness=2,
            highlightbackground=BG,
            highlightcolor=FOCUS,
            takefocus=True,
            padx=8,
            pady=2,
        )
        self.hotkey_btn.pack(side="left", padx=(12, 0))
        tk.Label(
            shell, text="Snip2MD", font=self.font_title, fg=TEXT, bg=BG
        ).pack(anchor="w", pady=(2, 18))

        timeline = tk.Frame(shell, bg=BG)
        timeline.pack(fill="both", expand=True)

        self.rail = tk.Canvas(timeline, width=18, bg=BG, highlightthickness=0)
        self.rail.pack(side="left", fill="y", padx=(2, 12))
        self.rail.bind("<Configure>", lambda _e: self._draw_rail())

        cards = tk.Frame(timeline, bg=BG)
        cards.pack(side="left", fill="both", expand=True)

        self.status_card = Card(cards)
        self.status_card.pack(fill="x")
        title_row = tk.Frame(self.status_card, bg=CARD)
        title_row.pack(fill="x")
        tk.Label(
            title_row,
            text="Ready",
            font=self.font_card,
            fg=TEXT,
            bg=CARD,
        ).pack(side="left")
        self.dot = tk.Canvas(
            title_row, width=14, height=14, bg=CARD, highlightthickness=0
        )
        self.dot.pack(side="right")
        self.status_var = tk.StringVar(value="Checking sign-in…")
        tk.Label(
            self.status_card,
            textvariable=self.status_var,
            font=self.font_body,
            fg=MUTED,
            bg=CARD,
            wraplength=280,
            justify="left",
        ).pack(anchor="w", pady=(6, 8))
        self.polish_var = tk.BooleanVar(value=ai_polish_enabled())
        tk.Checkbutton(
            self.status_card,
            text="AI polish for charts",
            variable=self.polish_var,
            command=self._on_polish_toggle,
            font=self.font_small,
            fg=MUTED,
            bg=CARD,
            selectcolor=WHITE,
            activebackground=CARD,
            activeforeground=TEXT,
            highlightthickness=0,
        ).pack(anchor="w")
        tk.Label(
            self.status_card,
            text="Subscription",
            font=self.font_small,
            fg=MUTED,
            bg=CARD,
        ).pack(anchor="w", pady=(10, 4))
        self.provider_var = tk.StringVar(value=preferred_provider())
        self._provider_btns: dict[str, tk.Button] = {}
        seg = tk.Frame(self.status_card, bg="#E8EEF4")
        seg.pack(fill="x")
        for key, label in (("auto", "Auto"), ("cursor", "Cursor"), ("claude", "Claude")):
            btn = tk.Button(
                seg,
                text=label,
                font=self.font_small,
                command=lambda name=key: self._on_provider(name),
                relief="flat",
                cursor="hand2",
                highlightthickness=2,
                highlightbackground="#E8EEF4",
                highlightcolor=FOCUS,
                takefocus=True,
                padx=8,
                pady=8,
            )
            btn.pack(side="left", fill="x", expand=True, padx=2, pady=2)
            self._provider_btns[key] = btn

        self.preview_card = Card(cards)
        self.preview_card.pack(fill="both", expand=True, pady=(12, 0))
        head = tk.Frame(self.preview_card, bg=CARD)
        head.pack(fill="x")
        self.preview_title = tk.Label(
            head,
            text="Last snip",
            font=self.font_card,
            fg=TEXT,
            bg=CARD,
        )
        self.preview_title.pack(side="left")
        self.copied_var = tk.StringVar(value="")
        self.copied_label = tk.Label(
            head,
            textvariable=self.copied_var,
            font=self.font_small,
            fg=MUTED,
            bg=CARD,
        )
        self.copied_label.pack(side="right")
        self.preview = tk.Text(
            self.preview_card,
            font=self.font_mono,
            bg=CARD,
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground=ACCENT,
            selectforeground=WHITE,
            relief="flat",
            wrap="word",
            padx=0,
            pady=8,
            height=12,
            highlightthickness=0,
            bd=0,
        )
        self.preview.pack(fill="both", expand=True)
        self.preview.insert("1.0", "Snip a heading, a table, or a list.\nMarkdown is copied automatically.")
        self.preview.configure(state="disabled")

        self.auth_host = tk.Frame(cards, bg=BG)
        self.auth_host.pack(fill="x", pady=(12, 0))

        self.cursor_btn = tk.Button(
            self.auth_host,
            text="Sign in with Cursor",
            font=self.font_button,
            command=self.sign_in_cursor,
            bg=ACCENT,
            fg=WHITE,
            activebackground=ACCENT_HOVER,
            activeforeground=WHITE,
            relief="flat",
            cursor="hand2",
            highlightthickness=2,
            highlightbackground=BG,
            highlightcolor=FOCUS,
            takefocus=True,
        )
        self.login_btn = tk.Button(
            self.auth_host,
            text="Sign in with Claude",
            font=self.font_body,
            command=self.sign_in_claude,
            bg=CARD,
            fg=TEXT,
            activebackground="#EEF2F5",
            activeforeground=TEXT,
            relief="flat",
            cursor="hand2",
            highlightthickness=2,
            highlightbackground=BG,
            highlightcolor=FOCUS,
            takefocus=True,
        )

        footer = tk.Frame(shell, bg=BG)
        footer.pack(fill="x", pady=(16, 0))
        self.copy_btn = tk.Button(
            footer,
            text="Copy again",
            font=self.font_body,
            command=self.copy_last,
            bg=CARD,
            fg=ACCENT,
            activebackground=WHITE,
            activeforeground=ACCENT_HOVER,
            relief="flat",
            cursor="hand2",
            highlightthickness=2,
            highlightbackground=BG,
            highlightcolor=FOCUS,
            takefocus=True,
            padx=14,
            pady=10,
        )
        self.copy_btn.pack(side="left", pady=8)

        self.snip_btn = tk.Button(
            footer,
            text="+",
            font=self.font_fab,
            command=self.start_snip,
            bg=ACCENT,
            fg=WHITE,
            activebackground=ACCENT_HOVER,
            activeforeground=WHITE,
            relief="flat",
            cursor="hand2",
            width=3,
            highlightthickness=2,
            highlightbackground=BG,
            highlightcolor=FOCUS,
            takefocus=True,
        )
        self.snip_btn.pack(side="right", ipadx=8, ipady=4)

        self.note_var = tk.StringVar(value="Esc cancels a snip.")
        tk.Label(
            shell,
            textvariable=self.note_var,
            font=self.font_small,
            fg=MUTED,
            bg=BG,
            wraplength=340,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

        self.root.after(80, self._draw_rail)

    def _draw_rail(self) -> None:
        self.rail.delete("all")
        height = max(self.rail.winfo_height(), 40)
        x = 8
        self.rail.create_line(x, 18, x, height - 18, fill=LINE, width=3)
        active_y = 28 if not self._card_active else height * 0.42
        self.rail.create_oval(x - 5, 22, x + 5, 32, fill=LINE, outline=LINE)
        self.rail.create_oval(
            x - 7,
            active_y - 7,
            x + 7,
            active_y + 7,
            fill=ACCENT,
            outline=WHITE,
            width=3,
        )

    def _bind_keys(self) -> None:
        self.root.bind("<Return>", lambda _e: self.start_snip())
        self.root.bind("<Control-c>", lambda _e: self.copy_last())
        if sys.platform == "darwin":
            self.root.bind("<Command-c>", lambda _e: self.copy_last())
        self.root.bind("<Escape>", self._on_escape)
        self.root.bind("<KeyPress>", self._on_hotkey_key)

    def _set_dot(self, color: str) -> None:
        self.dot.delete("all")
        self.dot.create_oval(2, 2, 12, 12, fill=color, outline=color)

    def _can_snip(self) -> bool:
        return True

    def _on_polish_toggle(self) -> None:
        set_ai_polish(bool(self.polish_var.get()))
        if ai_polish_enabled() and has_cursor_subscription():
            threading.Thread(target=warmup_cursor, daemon=True).start()
        self.refresh_status()

    def _on_provider(self, name: str) -> None:
        if name == "cursor" and not has_cursor_subscription():
            self.sign_in_cursor()
            return
        if name == "claude" and not has_claude_subscription():
            self.sign_in_claude()
            return
        try:
            set_preferred_provider(name)
        except ProviderError as exc:
            self.note_var.set(str(exc))
            return
        self.provider_var.set(name)
        if name == "cursor":
            threading.Thread(target=warmup_cursor, daemon=True).start()
        self.refresh_status()

    def _paint_provider(self) -> None:
        chosen = preferred_provider()
        self.provider_var.set(chosen)
        cursor_ok = has_cursor_subscription()
        claude_ok = has_claude_subscription()
        signed = {"auto": True, "cursor": cursor_ok, "claude": claude_ok}
        for key, btn in self._provider_btns.items():
            selected = key == chosen
            ready = signed[key]
            if selected:
                btn.configure(bg=ACCENT, fg=WHITE, activebackground=ACCENT_HOVER, activeforeground=WHITE)
            elif ready:
                btn.configure(bg=WHITE, fg=TEXT, activebackground="#EEF2F5", activeforeground=TEXT)
            else:
                btn.configure(bg=WHITE, fg=MUTED, activebackground="#EEF2F5", activeforeground=MUTED)

    def _can_polish(self) -> bool:
        if not ai_polish_enabled():
            return False
        try:
            active_provider_name()
            return True
        except ProviderError:
            return False

    def refresh_status(self) -> None:
        cursor_ok = has_cursor_subscription()
        claude_ok = has_claude_subscription()
        self._paint_provider()
        self.cursor_btn.pack_forget()
        self.login_btn.pack_forget()
        if self._can_polish():
            self._set_dot(OK)
            pref = preferred_provider()
            via = subscription_label()
            if pref == "auto":
                self.status_var.set(f"OCR ready · Auto polish via {via}")
            else:
                self.status_var.set(f"OCR ready · AI polish via {via}")
        elif not ai_polish_enabled():
            self._set_dot(OK)
            self.status_var.set("Local OCR. Snip copies Markdown automatically.")
        else:
            self._set_dot(OK)
            pref = preferred_provider()
            if pref == "cursor" and not cursor_ok:
                self.status_var.set("Local OCR. Sign in with Cursor to polish.")
            elif pref == "claude" and not claude_ok:
                self.status_var.set("Local OCR. Sign in with Claude to polish.")
            else:
                self.status_var.set("Local OCR. Sign in to polish charts with AI.")
        if not self.busy:
            self.snip_btn.configure(state="normal")
        if not cursor_ok:
            self.cursor_btn.pack(fill="x", ipady=8)
        if not claude_ok:
            self.login_btn.pack(fill="x", pady=(8, 0), ipady=8)

    def sign_in_cursor(self) -> None:
        try:
            start_cursor_login()
        except ProviderError as exc:
            self.note_var.set(str(exc).split("\n")[0])
            return
        self.note_var.set("Finish Cursor sign-in in the browser window that opened.")
        self.root.after(1500, self.refresh_status)

    def sign_in_claude(self) -> None:
        try:
            start_claude_login()
        except ProviderError as exc:
            self.note_var.set(str(exc).split("\n")[0])
            return
        self.note_var.set("Finish sign-in in the terminal window that just opened.")
        self.root.after(1500, self.refresh_status)

    def _warmup(self) -> None:
        self.note_var.set("Loading OCR so the first snip is quicker…")
        threading.Thread(target=self._warmup_worker, daemon=True).start()

    def _warmup_worker(self) -> None:
        warmup_ocr()
        if ai_polish_enabled() and has_cursor_subscription():
            warmup_cursor()
        self.root.after(0, self._warmup_done)

    def _warmup_done(self) -> None:
        if not self.busy:
            self.note_var.set("Snip copies to the clipboard. Esc cancels.")

    def _poll_login(self) -> None:
        if not self.busy:
            self.refresh_status()
        self.root.after(5000, self._poll_login)

    def _on_escape(self, _event=None):
        if self._capturing_hotkey:
            self._cancel_hotkey_capture()
            return "break"
        return None

    def _toggle_hotkey_capture(self) -> None:
        if self._capturing_hotkey:
            self._cancel_hotkey_capture()
            return
        self._capturing_hotkey = True
        self._unregister_hotkey()
        self.hotkey_var.set("Press the new shortcut…")
        self.hotkey_btn.configure(text="Cancel")
        self.note_var.set(hotkey_capture_hint())

    def _cancel_hotkey_capture(self) -> None:
        self._capturing_hotkey = False
        self.hotkey_btn.configure(text="Change shortcut")
        self._apply_hotkey(self._hotkey_spec, persist=False)
        self.note_var.set("Shortcut unchanged.")

    def _on_hotkey_key(self, event):
        if not self._capturing_hotkey:
            return None
        spec = hotkey_from_event(event)
        if spec is None:
            return "break"
        try:
            parse_hotkey(spec)
        except ProviderError as exc:
            self.note_var.set(str(exc))
            return "break"
        self._capturing_hotkey = False
        self.hotkey_btn.configure(text="Change shortcut")
        if not self._apply_hotkey(spec, persist=True):
            self.note_var.set("That shortcut is already in use. Try another.")
            self._apply_hotkey(self._hotkey_spec, persist=False)
            return "break"
        self.note_var.set(f"Shortcut saved: {format_hotkey(spec)}")
        return "break"

    def _unregister_hotkey(self) -> None:
        self._hotkey.unregister()
        self.hotkey_ok = False

    def _apply_hotkey(self, spec: str, *, persist: bool) -> bool:
        try:
            parse_hotkey(spec)
        except ProviderError:
            self.hotkey_var.set("Shortcut unavailable — use +")
            return False
        if not self._hotkey.register(spec):
            self.hotkey_ok = False
            self.hotkey_var.set(format_hotkey(spec) + " in use — use +")
            return False
        self.hotkey_ok = True
        self._hotkey_spec = spec
        self.hotkey_var.set(format_hotkey(spec))
        if persist:
            set_hotkey(spec)
        return True

    def _register_hotkey(self) -> None:
        self._apply_hotkey(self._hotkey_spec, persist=False)

    def _poll_hotkey(self) -> None:
        try:
            if not self.root.winfo_exists():
                return
        except tk.TclError:
            return
        if self._hotkey.fired.is_set():
            self._hotkey.fired.clear()
            self.start_snip()
        self.root.after(16, self._poll_hotkey)

    def start_snip(self) -> None:
        if self.busy or self._capturing_hotkey:
            return
        self.busy = True
        self.snip_btn.configure(state="disabled", text="…")
        self.note_var.set("Drag a rectangle. Esc cancels.")
        self.root.withdraw()
        self.root.after(70, self._run_overlay)

    def _run_overlay(self) -> None:
        overlay = SnipOverlay(self.root)
        bbox = overlay.run()
        img = None
        grab_error = None
        if bbox is not None:
            try:
                img = grab_region(bbox)
            except Exception as exc:
                grab_error = exc
        self.root.deiconify()
        self.root.lift()
        if bbox is None:
            self._idle("Cancelled.")
            return
        if img is None:
            if isinstance(grab_error, ProviderError):
                self._idle(str(grab_error))
            else:
                self._idle(
                    public_error_message(grab_error or Exception("Could not capture."))
                )
            return
        self.snip_btn.configure(text="…")
        self.note_var.set("Reading text locally…")
        polish = self._can_polish()
        threading.Thread(
            target=self._convert, args=(img, polish), daemon=True
        ).start()

    def _push_clipboard(self, markdown: str, *, replace: str | None = None) -> bool:
        """Copy markdown. If replace is set, do not clobber a different clipboard."""
        try:
            if replace:
                try:
                    current = pyperclip.paste()
                except Exception:
                    current = replace
                if current not in (replace, markdown):
                    return False
            pyperclip.copy(markdown)
            return True
        except Exception:
            return False

    def _convert(self, img, polish: bool) -> None:
        self._snip_id += 1
        snip_id = self._snip_id
        emitted: dict[str, str] = {"md": ""}

        def on_markdown(text: str) -> None:
            first = not emitted["md"]
            previous = emitted["md"]
            emitted["md"] = text
            if first:
                self._push_clipboard(text)
            else:
                self._push_clipboard(text, replace=previous)
            self.root.after(
                0,
                lambda t=text, flag=first: self._on_ocr_progress(
                    snip_id, t, flag, img, polish
                ),
            )

        try:
            markdown = image_to_markdown_ocr(img, on_markdown=on_markdown)
            error = None
        except ProviderError as exc:
            markdown = ""
            error = str(exc)
        except Exception as exc:
            markdown = ""
            error = public_error_message(exc)
        self.root.after(
            0,
            lambda: self._finish_ocr(
                snip_id, emitted["md"] or markdown, error, polish
            ),
        )

    def _on_ocr_progress(
        self,
        snip_id: int,
        markdown: str,
        first: bool,
        img,
        polish: bool,
    ) -> None:
        if snip_id != self._snip_id:
            return
        self.last_markdown = markdown
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", markdown)
        self.preview.configure(state="disabled")
        if first:
            self._flash_copied()
            self.note_var.set(f"Copied. {paste_shortcut()} to paste.")
            if polish:
                threading.Thread(
                    target=self._polish, args=(snip_id, img, markdown), daemon=True
                ).start()

    def _finish_ocr(
        self,
        snip_id: int,
        markdown: str,
        error: str | None,
        polish: bool,
    ) -> None:
        if snip_id != self._snip_id:
            return
        if error:
            self._idle(error)
            return
        if not markdown:
            self._idle("Nothing came back. Try snipping a bit more.")
            return
        if self.last_markdown != markdown:
            copied = self._apply_markdown(markdown)
            if not copied:
                self._idle("Markdown is ready, but clipboard copy failed. Use Copy again.")
                return
        if polish:
            self._idle("On the clipboard. AI polish running in the background…")
        else:
            self._idle(f"On the clipboard. {paste_shortcut()} to paste.")

    def _polish(self, snip_id: int, img, ocr_markdown: str) -> None:
        started = time.monotonic()
        try:
            payload, mime, size = encode_screenshot(img)
            markdown = image_to_markdown(
                payload, mime, size, ocr_markdown=ocr_markdown
            )
            error = None
        except ProviderError as exc:
            markdown = ""
            error = str(exc)
        except Exception as exc:
            markdown = ""
            error = public_error_message(exc)
        elapsed = time.monotonic() - started
        self.root.after(
            0, lambda: self._finish_polish(snip_id, markdown, error, elapsed)
        )

    def _finish_polish(
        self, snip_id: int, markdown: str, error: str | None, elapsed: float
    ) -> None:
        if snip_id != self._snip_id:
            return
        if error or not markdown:
            self.note_var.set(
                "OCR is on the clipboard. AI polish failed — paste that, or snip again."
            )
            return
        if self._apply_markdown(markdown):
            self.note_var.set(
                f"Copied structured Markdown in {elapsed:.1f}s. {paste_shortcut()} to paste."
            )
        else:
            self.note_var.set(
                "Polish is in the window, but clipboard copy failed. Use Copy again."
            )

    def _set_preview_theme(self, active: bool) -> None:
        self._card_active = active
        fill = CARD_ACTIVE if active else CARD
        fg = WHITE if active else TEXT
        muted = "#E8F1FB" if active else MUTED
        self.preview_card.set_active(active)
        self.preview_title.configure(
            bg=fill, fg=fg, text="Copied" if active else "Last snip"
        )
        self.copied_label.configure(bg=fill, fg=muted)
        self.preview.configure(bg=fill, fg=fg, insertbackground=fg)
        self._draw_rail()

    def _flash_copied(self) -> None:
        self.copied_var.set(paste_shortcut())
        self._set_preview_theme(True)
        if self._copied_job is not None:
            self.root.after_cancel(self._copied_job)
        self._copied_job = self.root.after(2500, self._restore_preview_theme)

    def _restore_preview_theme(self) -> None:
        self._copied_job = None
        self.copied_var.set("on clipboard")
        self._set_preview_theme(False)
        if self.last_markdown:
            self.preview_title.configure(text="Last snip")

    def _apply_markdown(self, markdown: str) -> bool:
        self.last_markdown = markdown
        try:
            pyperclip.copy(markdown)
        except Exception:
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", markdown)
            self.preview.configure(state="disabled")
            return False
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", markdown)
        self.preview.configure(state="disabled")
        self._flash_copied()
        return True

    def _idle(self, note: str) -> None:
        self.busy = False
        ready = self._can_snip()
        self.snip_btn.configure(
            state="normal" if ready else "disabled",
            text="+",
        )
        self.note_var.set(note)

    def copy_last(self) -> None:
        if not self.last_markdown:
            self.note_var.set("Nothing to copy yet.")
            return
        if self._apply_markdown(self.last_markdown):
            self.note_var.set(f"Copied again. {paste_shortcut()} to paste.")
        else:
            self.note_var.set("Clipboard copy failed. Select the text and copy.")

    def _quit(self) -> None:
        self._hotkey.close()
        close_cursor_runtime()
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def run_app() -> int:
    if not claim_single_instance():
        focus_existing_window()
        return 0
    reap_snip2md_bridges()
    register_runtime_shutdown()
    return Snip2MdApp().run()

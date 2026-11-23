#!/usr/bin/env python3
"""
OpenClip UI
Dark-themed popup (Super+V) showing clipboard history.
Clicking an item pastes it; Escape closes the window.

Toolkit: Tkinter + Pillow
"""

import io
import logging
import os
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

sys.path.insert(0, str(Path(__file__).parent))

from security import (
    DATA_DIR,
    SecurityManager,
    ensure_data_dir,
    load_history,
)

try:
    from PIL import Image, ImageTk, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
THEME = {
    "bg": "#1e1e2e",
    "bg2": "#2a2a3e",
    "bg_hover": "#3a3a5e",
    "fg": "#cdd6f4",
    "fg_dim": "#7f849c",
    "accent": "#89b4fa",
    "border": "#45475a",
    "scrollbar": "#45475a",
    "font_main": ("Segoe UI", 11) if sys.platform == "win32" else ("Ubuntu", 11),
    "font_small": ("Segoe UI", 9) if sys.platform == "win32" else ("Ubuntu", 9),
    "radius": 8,
}

THUMB_W, THUMB_H = 180, 100
MAX_DISPLAY = 50
_WAYLAND = bool(os.environ.get("WAYLAND_DISPLAY"))


# ---------------------------------------------------------------------------
# Paste helpers
# ---------------------------------------------------------------------------

def _copy_to_clipboard(text: str | None = None, image_bytes: bytes | None = None):
    """Write text or image back to the system clipboard."""
    try:
        if text is not None:
            if _WAYLAND:
                proc = subprocess.Popen(
                    ["wl-copy"],
                    stdin=subprocess.PIPE,
                )
            else:
                proc = subprocess.Popen(
                    ["xclip", "-selection", "clipboard"],
                    stdin=subprocess.PIPE,
                )
            proc.communicate(input=text.encode("utf-8"))

        elif image_bytes is not None:
            if _WAYLAND:
                proc = subprocess.Popen(
                    ["wl-copy", "--type", "image/png"],
                    stdin=subprocess.PIPE,
                )
            else:
                proc = subprocess.Popen(
                    ["xclip", "-selection", "clipboard", "-t", "image/png"],
                    stdin=subprocess.PIPE,
                )
            proc.communicate(input=image_bytes)
    except FileNotFoundError as exc:
        log.error("Clipboard tool not found: %s", exc)


def _trigger_paste():
    """Simulate Ctrl+V in the previously focused application."""
    time.sleep(0.12)   # let the window close / focus return
    try:
        if _WAYLAND:
            subprocess.run(["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"], timeout=3)
        else:
            subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], timeout=3)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("Auto-paste failed: %s", exc)


# ---------------------------------------------------------------------------
# Main popup window
# ---------------------------------------------------------------------------

class OpenClipUI:
    def __init__(self):
        ensure_data_dir()
        self.sec = SecurityManager()
        self.history = load_history(self.sec)

        self.root = tk.Tk()
        self.root.title("OpenClip")
        self.root.overrideredirect(True)       # frameless
        self.root.attributes("-topmost", True) # always on top
        self.root.configure(bg=THEME["bg"])

        self._image_refs: list = []  # keep Tk image refs alive

        self._build_ui()
        self._position_window()
        self.root.bind("<Escape>", lambda _e: self.root.destroy())
        self.root.bind("<FocusOut>", self._on_focus_out)
        self.root.focus_force()

    # ------------------------------------------------------------------
    # Window positioning
    # ------------------------------------------------------------------

    def _position_window(self):
        """Place the popup near the bottom-right of the primary screen."""
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        ww = self.root.winfo_reqwidth()
        wh = self.root.winfo_reqheight()
        x = sw - ww - 24
        y = sh - wh - 60
        self.root.geometry(f"{ww}x{wh}+{x}+{y}")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Header bar
        header = tk.Frame(self.root, bg=THEME["bg2"], pady=8)
        header.pack(fill=tk.X)
        tk.Label(
            header,
            text="📋  Clipboard History",
            bg=THEME["bg2"],
            fg=THEME["accent"],
            font=(*THEME["font_main"], "bold"),
        ).pack(side=tk.LEFT, padx=14)
        tk.Button(
            header,
            text="✕",
            bg=THEME["bg2"],
            fg=THEME["fg_dim"],
            activebackground=THEME["bg_hover"],
            activeforeground=THEME["fg"],
            relief=tk.FLAT,
            bd=0,
            padx=10,
            command=self.root.destroy,
        ).pack(side=tk.RIGHT, padx=4)

        # Scrollable canvas
        container = tk.Frame(self.root, bg=THEME["bg"])
        container.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(
            container,
            bg=THEME["bg"],
            highlightthickness=0,
            width=420,
            height=min(60 * len(self.history) + 10, 480),
        )
        scrollbar = tk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = tk.Frame(canvas, bg=THEME["bg"])
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(window_id, width=canvas.winfo_width())

        inner.bind("<Configure>", _on_configure)
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        canvas.bind("<Button-4>", lambda _e: canvas.yview_scroll(-1, "units"))
        canvas.bind("<Button-5>", lambda _e: canvas.yview_scroll(1, "units"))

        if not self.history:
            tk.Label(
                inner,
                text="No clipboard history yet.\nCopy something to get started!",
                bg=THEME["bg"],
                fg=THEME["fg_dim"],
                font=THEME["font_main"],
                pady=40,
            ).pack()
            return

        for idx, entry in enumerate(self.history[:MAX_DISPLAY]):
            self._build_entry_row(inner, idx, entry)

    def _build_entry_row(self, parent: tk.Frame, idx: int, entry: dict):
        """Build a single history row."""
        row = tk.Frame(parent, bg=THEME["bg"], pady=2, padx=6)
        row.pack(fill=tk.X)

        # Separator (except first)
        if idx > 0:
            sep = tk.Frame(row, bg=THEME["border"], height=1)
            sep.pack(fill=tk.X, pady=(0, 2))

        card = tk.Frame(row, bg=THEME["bg2"], padx=10, pady=8)
        card.pack(fill=tk.X)

        if entry.get("type") == "image":
            self._build_image_card(card, entry)
        else:
            self._build_text_card(card, entry)

        # Hover effects
        def _on_enter(e, f=card):
            f.configure(bg=THEME["bg_hover"])
            for child in f.winfo_children():
                try:
                    child.configure(bg=THEME["bg_hover"])
                except tk.TclError:
                    pass

        def _on_leave(e, f=card):
            f.configure(bg=THEME["bg2"])
            for child in f.winfo_children():
                try:
                    child.configure(bg=THEME["bg2"])
                except tk.TclError:
                    pass

        def _on_click(e, entry=entry):
            self._select_entry(entry)

        for widget in [card] + list(card.winfo_children()):
            widget.bind("<Enter>", _on_enter)
            widget.bind("<Leave>", _on_leave)
            widget.bind("<Button-1>", _on_click)

    def _build_text_card(self, card: tk.Frame, entry: dict):
        preview = entry.get("preview", "")
        # Truncate long previews
        display = preview[:300].replace("\n", " ↵ ") if preview else "(empty)"
        label = tk.Label(
            card,
            text=display,
            bg=THEME["bg2"],
            fg=THEME["fg"],
            font=THEME["font_main"],
            anchor="w",
            justify=tk.LEFT,
            wraplength=380,
        )
        label.pack(fill=tk.X, anchor="w")
        # Timestamp
        ts = entry.get("timestamp", 0)
        time_str = time.strftime("%H:%M", time.localtime(ts)) if ts else ""
        tk.Label(
            card,
            text=time_str,
            bg=THEME["bg2"],
            fg=THEME["fg_dim"],
            font=THEME["font_small"],
            anchor="e",
        ).pack(fill=tk.X, anchor="e")

    def _build_image_card(self, card: tk.Frame, entry: dict):
        enc_path = Path(entry.get("enc_path", ""))
        ts = entry.get("timestamp", 0)
        time_str = time.strftime("%H:%M", time.localtime(ts)) if ts else ""

        if PIL_AVAILABLE and enc_path.exists():
            try:
                raw = self.sec.load_image(enc_path)
                img = Image.open(io.BytesIO(raw))
                img.thumbnail((THUMB_W, THUMB_H), Image.LANCZOS)
                tk_img = ImageTk.PhotoImage(img)
                self._image_refs.append(tk_img)
                img_label = tk.Label(card, image=tk_img, bg=THEME["bg2"])
                img_label.pack(anchor="w")
            except Exception as exc:
                log.warning("Could not render thumbnail: %s", exc)
                tk.Label(
                    card,
                    text="🖼  [Image]",
                    bg=THEME["bg2"],
                    fg=THEME["fg"],
                    font=THEME["font_main"],
                ).pack(anchor="w")
        else:
            tk.Label(
                card,
                text="🖼  [Image]",
                bg=THEME["bg2"],
                fg=THEME["fg"],
                font=THEME["font_main"],
            ).pack(anchor="w")

        tk.Label(
            card,
            text=time_str,
            bg=THEME["bg2"],
            fg=THEME["fg_dim"],
            font=THEME["font_small"],
            anchor="e",
        ).pack(fill=tk.X, anchor="e")

    # ------------------------------------------------------------------
    # Entry selection / paste
    # ------------------------------------------------------------------

    def _select_entry(self, entry: dict):
        """Copy the selected entry to clipboard, close popup, and trigger paste."""
        if entry.get("type") == "text":
            text = entry.get("preview", "")
            _copy_to_clipboard(text=text)
        elif entry.get("type") == "image":
            enc_path = Path(entry.get("enc_path", ""))
            if enc_path.exists():
                try:
                    raw = self.sec.load_image(enc_path)
                    _copy_to_clipboard(image_bytes=raw)
                except Exception as exc:
                    log.error("Could not load image for paste: %s", exc)
                    return

        self.root.destroy()
        _trigger_paste()

    # ------------------------------------------------------------------

    def _on_focus_out(self, event):
        """Close popup when it loses focus (click elsewhere)."""
        # Small delay to let click events register first
        self.root.after(150, self.root.destroy)

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    app = OpenClipUI()
    app.run()


if __name__ == "__main__":
    main()

"""
OpenClip UI Module
Dark-themed, Windows-style (Super+V) clipboard popup.
Features:
- Instant toggle via resident IPC or standalone mode
- Single-click auto-paste directly into previous active window
- Universal smooth scrolling (mouse wheel + touchpad)
- Real-time live updates when new content is copied
- Strict single-instance management (no duplicate/stale windows)
- Always-on-top frameless overlay at bottom-right of screen
"""

import io
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

sys.path.insert(0, str(Path(__file__).parent))

from security import DATA_DIR, SecurityManager, ensure_data_dir, load_history
import x11_helper

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & Theme (Windows 11 inspired dark fluent theme)
# ---------------------------------------------------------------------------
SOCKET_PATH = f"/tmp/openclip_{os.getuid()}.sock"
POPUP_WIDTH = 380
POPUP_HEIGHT = 500
MAX_DISPLAY = 50
THUMB_MAX_W = 200
THUMB_MAX_H = 110

THEME = {
    "bg": "#181825",          # Deep dark base
    "header_bg": "#11111b",   # Dark header bar
    "card_bg": "#252538",     # Card background
    "card_hover": "#363654",  # Card hover highlight
    "fg": "#cdd6f4",          # Primary text
    "fg_dim": "#9399b2",      # Secondary / timestamp text
    "accent": "#89b4fa",      # Accent blue
    "border": "#313244",      # Subtle borders
    "badge_bg": "#1e1e2e",    # Tag / badge background
    "font_family": "Ubuntu" if sys.platform != "win32" else "Segoe UI",
}


# ---------------------------------------------------------------------------
# UI Popup Class
# ---------------------------------------------------------------------------

class OpenClipPopup:
    def __init__(self, sec: SecurityManager, history_getter=None, paste_callback=None):
        self.sec = sec
        self.get_history = history_getter or (lambda: load_history(self.sec))
        self.on_paste_selected = paste_callback

        self.root = tk.Tk()
        self.root.title("OpenClip")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=THEME["bg"])

        # State
        self.is_visible = False
        self.target_window_id: int | None = None
        self._image_cache: dict[str, ImageTk.PhotoImage] = {}
        self._card_widgets: list[tk.Widget] = []

        self._build_scaffold()
        self._bind_global_events()

        # Start hidden by default (ready for instant reveal)
        self.root.withdraw()

    # ------------------------------------------------------------------
    # UI Scaffold & Construction
    # ------------------------------------------------------------------

    def _build_scaffold(self):
        # Header
        self.header = tk.Frame(self.root, bg=THEME["header_bg"], height=42, padx=14)
        self.header.pack(fill=tk.X, side=tk.TOP)
        self.header.pack_propagate(False)

        title_lbl = tk.Label(
            self.header,
            text="📋  Clipboard",
            bg=THEME["header_bg"],
            fg=THEME["accent"],
            font=(THEME["font_family"], 11, "bold"),
        )
        title_lbl.pack(side=tk.LEFT, pady=10)

        self.count_badge = tk.Label(
            self.header,
            text="0 items",
            bg=THEME["badge_bg"],
            fg=THEME["fg_dim"],
            font=(THEME["font_family"], 8),
            padx=6,
            pady=2,
        )
        self.count_badge.pack(side=tk.LEFT, padx=8, pady=10)

        close_btn = tk.Label(
            self.header,
            text="✕",
            bg=THEME["header_bg"],
            fg=THEME["fg_dim"],
            font=(THEME["font_family"], 11),
            cursor="hand2",
            padx=8,
        )
        close_btn.pack(side=tk.RIGHT, pady=8)
        close_btn.bind("<Button-1>", lambda _e: self.hide())
        close_btn.bind("<Enter>", lambda _e: close_btn.configure(fg=THEME["fg"], bg=THEME["card_hover"]))
        close_btn.bind("<Leave>", lambda _e: close_btn.configure(fg=THEME["fg_dim"], bg=THEME["header_bg"]))

        # Scrollable Canvas
        self.container = tk.Frame(self.root, bg=THEME["bg"])
        self.container.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(
            self.container,
            bg=THEME["bg"],
            highlightthickness=0,
            bd=0,
        )
        self.scrollbar = tk.Scrollbar(
            self.container,
            orient=tk.VERTICAL,
            command=self.canvas.yview,
            width=10,
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.inner_frame = tk.Frame(self.canvas, bg=THEME["bg"])
        self.canvas_win = self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")

        self.inner_frame.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self.canvas_win, width=e.width)
        )

        # Universal Mousewheel / Trackpad Scrolling
        self._bind_mouse_scroll(self.root)
        self._bind_mouse_scroll(self.canvas)
        self._bind_mouse_scroll(self.inner_frame)

    def _bind_mouse_scroll(self, widget):
        """Recursively bind smooth scrolling to widget and all future children."""
        def _scroll_up(event=None):
            self.canvas.yview_scroll(-3, "units")

        def _scroll_down(event=None):
            self.canvas.yview_scroll(3, "units")

        def _mouse_wheel(event):
            if event.num == 4 or (hasattr(event, "delta") and event.delta > 0):
                self.canvas.yview_scroll(-3, "units")
            elif event.num == 5 or (hasattr(event, "delta") and event.delta < 0):
                self.canvas.yview_scroll(3, "units")

        # Linux X11 uses Button-4 (up) and Button-5 (down)
        widget.bind("<Button-4>", _scroll_up, add="+")
        widget.bind("<Button-5>", _scroll_down, add="+")
        widget.bind("<MouseWheel>", _mouse_wheel, add="+")

    def _bind_global_events(self):
        # Escape closes popup
        self.root.bind("<Escape>", lambda _e: self.hide())

        # Auto-hide on loss of focus
        def _on_focus_out(event):
            # If focus moved outside our top-level window, hide
            self.root.after(100, self._check_focus)

        self.root.bind("<FocusOut>", _on_focus_out)

    def _check_focus(self):
        if not self.is_visible:
            return
        try:
            focused = self.root.focus_get()
            if focused is None:
                self.hide()
        except Exception:
            self.hide()

    # ------------------------------------------------------------------
    # Visibility & Positioning
    # ------------------------------------------------------------------

    def show(self, target_window_id: int | None = None):
        """Display the popup at bottom-right and refresh cards."""
        self.target_window_id = target_window_id
        self.is_visible = True

        self.refresh_cards()

        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = sw - POPUP_WIDTH - 20
        y = sh - POPUP_HEIGHT - 60

        self.root.geometry(f"{POPUP_WIDTH}x{POPUP_HEIGHT}+{x}+{y}")
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.canvas.yview_moveto(0)

    def hide(self):
        """Instantly hide the popup."""
        if not self.is_visible:
            return
        self.is_visible = False
        try:
            self.root.withdraw()
        except Exception:
            pass

    def toggle(self, target_window_id: int | None = None):
        """Toggle between visible and hidden."""
        if self.is_visible:
            self.hide()
        else:
            self.show(target_window_id=target_window_id)

    # ------------------------------------------------------------------
    # Card Rendering & Live Updating
    # ------------------------------------------------------------------

    def refresh_cards(self):
        """Re-render all history items."""
        # Clear existing cards
        for widget in self.inner_frame.winfo_children():
            widget.destroy()
        self._card_widgets.clear()

        history = self.get_history()
        count = len(history)
        self.count_badge.configure(text=f"{count} {'item' if count == 1 else 'items'}")

        if not history:
            empty_frame = tk.Frame(self.inner_frame, bg=THEME["bg"], pady=60)
            empty_frame.pack(fill=tk.BOTH, expand=True)
            self._bind_mouse_scroll(empty_frame)

            lbl = tk.Label(
                empty_frame,
                text="📋\n\nYour clipboard history is empty.\nCopy some text or screenshots!",
                bg=THEME["bg"],
                fg=THEME["fg_dim"],
                font=(THEME["font_family"], 10),
                justify=tk.CENTER,
            )
            lbl.pack()
            self._bind_mouse_scroll(lbl)
            return

        for idx, entry in enumerate(history[:MAX_DISPLAY]):
            self._create_card(entry, idx)

    def prepend_card(self, entry: dict):
        """Live update: prepend a newly copied entry to the top in real time."""
        if not self.is_visible:
            return

        # If currently showing empty message, refresh all
        if not self._card_widgets:
            self.refresh_cards()
            return

        card = self._render_card_widget(entry)
        card.pack(fill=tk.X, padx=10, pady=(6, 2), before=self._card_widgets[0] if self._card_widgets else None)
        self._card_widgets.insert(0, card)

        # Update count
        count = len(self._card_widgets)
        self.count_badge.configure(text=f"{count} {'item' if count == 1 else 'items'}")
        self.canvas.yview_moveto(0)

    def _create_card(self, entry: dict, idx: int):
        card = self._render_card_widget(entry)
        card.pack(fill=tk.X, padx=10, pady=(6, 2))
        self._card_widgets.append(card)

    def _render_card_widget(self, entry: dict) -> tk.Frame:
        entry_type = entry.get("type", "text")

        card = tk.Frame(
            self.inner_frame,
            bg=THEME["card_bg"],
            bd=1,
            relief=tk.FLAT,
            padx=12,
            pady=10,
            cursor="hand2",
        )

        # Content Frame
        content_frame = tk.Frame(card, bg=THEME["card_bg"])
        content_frame.pack(fill=tk.BOTH, expand=True)

        if entry_type == "image":
            self._render_image_content(content_frame, entry)
        else:
            self._render_text_content(content_frame, entry)

        # Footer (Badge & Timestamp)
        footer = tk.Frame(card, bg=THEME["card_bg"], pady=4)
        footer.pack(fill=tk.X, side=tk.BOTTOM)

        badge_text = "🖼 Image" if entry_type == "image" else "📄 Text"
        type_badge = tk.Label(
            footer,
            text=badge_text,
            bg=THEME["badge_bg"],
            fg=THEME["fg_dim"],
            font=(THEME["font_family"], 8),
            padx=6,
            pady=1,
        )
        type_badge.pack(side=tk.LEFT)

        ts = entry.get("timestamp", 0)
        time_str = self._format_timestamp(ts)
        time_lbl = tk.Label(
            footer,
            text=time_str,
            bg=THEME["card_bg"],
            fg=THEME["fg_dim"],
            font=(THEME["font_family"], 8),
        )
        time_lbl.pack(side=tk.RIGHT)

        # Interactions & Bindings
        all_card_widgets = [card, content_frame, footer, type_badge, time_lbl] + list(content_frame.winfo_children())

        def _on_hover(_e):
            card.configure(bg=THEME["card_hover"])
            content_frame.configure(bg=THEME["card_hover"])
            footer.configure(bg=THEME["card_hover"])
            time_lbl.configure(bg=THEME["card_hover"])

        def _on_leave(_e):
            card.configure(bg=THEME["card_bg"])
            content_frame.configure(bg=THEME["card_bg"])
            footer.configure(bg=THEME["card_bg"])
            time_lbl.configure(bg=THEME["card_bg"])

        def _on_click(_e, ent=entry):
            self._handle_item_click(ent)

        for w in all_card_widgets:
            w.bind("<Enter>", _on_hover)
            w.bind("<Leave>", _on_leave)
            w.bind("<Button-1>", _on_click)
            self._bind_mouse_scroll(w)

        return card

    def _render_text_content(self, parent: tk.Frame, entry: dict):
        text = entry.get("preview", "")
        # Show first ~4 lines neatly
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        display_text = "\n".join(lines[:4])
        if len(lines) > 4 or len(text) > 200:
            display_text += "..."

        lbl = tk.Label(
            parent,
            text=display_text,
            bg=THEME["card_bg"],
            fg=THEME["fg"],
            font=(THEME["font_family"], 10),
            justify=tk.LEFT,
            anchor="w",
            wraplength=POPUP_WIDTH - 60,
        )
        lbl.pack(fill=tk.X, anchor="w", pady=(0, 4))

    def _render_image_content(self, parent: tk.Frame, entry: dict):
        enc_path = Path(entry.get("enc_path", ""))
        rendered = False

        if PIL_AVAILABLE and enc_path.exists():
            try:
                raw_bytes = self.sec.load_image(enc_path)
                cache_key = str(enc_path)
                if cache_key not in self._image_cache:
                    pil_img = Image.open(io.BytesIO(raw_bytes))
                    pil_img.thumbnail((THUMB_MAX_W, THUMB_MAX_H), Image.LANCZOS)
                    self._image_cache[cache_key] = ImageTk.PhotoImage(pil_img)

                img_label = tk.Label(
                    parent,
                    image=self._image_cache[cache_key],
                    bg=THEME["card_bg"],
                )
                img_label.pack(anchor="w", pady=(0, 4))
                rendered = True
            except Exception as exc:
                log.warning("Thumbnail error: %s", exc)

        if not rendered:
            lbl = tk.Label(
                parent,
                text="[Encrypted Screenshot]",
                bg=THEME["card_bg"],
                fg=THEME["accent"],
                font=(THEME["font_family"], 10, "italic"),
            )
            lbl.pack(anchor="w", pady=(0, 4))

    def _format_timestamp(self, ts: float) -> str:
        if not ts:
            return ""
        delta = time.time() - ts
        if delta < 60:
            return "Just now"
        elif delta < 3600:
            mins = int(delta // 60)
            return f"{mins}m ago"
        elif delta < 86400:
            hours = int(delta // 3600)
            return f"{hours}h ago"
        return time.strftime("%b %d, %H:%M", time.localtime(ts))

    # ------------------------------------------------------------------
    # Selection & Auto-Paste Action
    # ------------------------------------------------------------------

    def _handle_item_click(self, entry: dict):
        """When an item is clicked, copy it, hide popup, and auto-paste into previous window."""
        log.info("Item selected for auto-paste (type=%s)", entry.get("type"))
        target_win = self.target_window_id

        # 1. Put onto system clipboard
        if entry.get("type") == "image":
            enc_path = Path(entry.get("enc_path", ""))
            if enc_path.exists():
                try:
                    raw = self.sec.load_image(enc_path)
                    x11_helper.copy_to_system_clipboard(image_bytes=raw)
                except Exception as exc:
                    log.error("Could not load image: %s", exc)
                    return
        else:
            text = entry.get("preview", "")
            x11_helper.copy_to_system_clipboard(text=text)

        # 2. Hide OpenClip popup immediately
        self.hide()

        # 3. Notify callback if registered
        if self.on_paste_selected:
            self.on_paste_selected(entry, target_win)
            return

        # Default paste execution
        def _execute_paste():
            if target_win:
                x11_helper.activate_window(target_win)
            # Give window manager 60ms to switch focus
            time.sleep(0.06)
            x11_helper.simulate_paste()

        threading.Thread(target=_execute_paste, daemon=True).start()

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------
# IPC Client (invoked by Super+V)
# ---------------------------------------------------------------------------

def send_ipc_command(cmd: str) -> bool:
    """Send command to resident daemon socket. Returns True on success."""
    if not os.path.exists(SOCKET_PATH):
        return False
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        sock.connect(SOCKET_PATH)
        sock.sendall(cmd.encode("utf-8") + b"\n")
        sock.close()
        return True
    except Exception as exc:
        log.debug("IPC failed: %s", exc)
        return False


def main():
    # 1. Get currently active window ID before anything else
    active_win = x11_helper.get_active_window()
    cmd = f"toggle:{active_win if active_win else 0}"

    # 2. Try sending toggle to running daemon
    if send_ipc_command(cmd):
        # Daemon handled it in <10ms!
        sys.exit(0)

    # 3. If daemon not responding, start daemon in background
    log.info("OpenClip daemon not running. Starting daemon...")
    daemon_script = Path(__file__).parent / "daemon.py"
    subprocess.Popen(
        [sys.executable, str(daemon_script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Wait up to 1.5s for socket to become ready
    for _ in range(30):
        time.sleep(0.05)
        if send_ipc_command(cmd):
            sys.exit(0)

    # 4. Fallback: run UI directly
    ensure_data_dir()
    sec = SecurityManager()
    popup = OpenClipPopup(sec)
    popup.show(target_window_id=active_win)
    popup.run()


if __name__ == "__main__":
    main()

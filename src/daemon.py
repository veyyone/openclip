#!/usr/bin/env python3
"""
OpenClip Daemon
Silent background service and IPC server for instant Super+V clipboard popup.

Features:
- Sub-second background monitoring (Wayland & X11)
- Encrypted storage at rest (Fernet AES + GNOME Keyring)
- Password manager secret filtering
- Real-time live updating of open clipboard popup
- Pre-warmed resident UI in memory (zero startup delay on Super+V)
- Reliable single-click auto-paste into previously active window
"""

import hashlib
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

# Ensure src/ is in path
sys.path.insert(0, str(Path(__file__).parent))

from security import (
    DATA_DIR,
    SecurityManager,
    ensure_data_dir,
    is_password_manager_content,
    load_history,
    save_history,
)
from ui import OpenClipPopup, SOCKET_PATH
import x11_helper

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
ensure_data_dir()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [openclip-daemon] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(DATA_DIR / "daemon.log", delay=True),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_HISTORY = 50
POLL_INTERVAL = 0.15  # 150ms for responsive clipboard capture
_WAYLAND = bool(os.environ.get("WAYLAND_DISPLAY"))


# ---------------------------------------------------------------------------
# Clipboard Retrieval Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=3, **kwargs)


def get_clipboard_mime_types() -> list[str]:
    """Return available MIME types currently on the clipboard."""
    try:
        if _WAYLAND:
            result = _run(["wl-paste", "--list-types"])
        else:
            result = _run(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"])
        if result.returncode != 0:
            return []
        return [t.strip() for t in result.stdout.decode(errors="replace").splitlines() if t.strip()]
    except Exception:
        return []


def get_clipboard_text() -> str | None:
    """Return current clipboard text, or None if unavailable."""
    try:
        if _WAYLAND:
            result = _run(["wl-paste", "--no-newline", "-t", "text/plain"])
        else:
            result = _run(["xclip", "-selection", "clipboard", "-t", "text/plain", "-o"])
        if result.returncode != 0 or not result.stdout:
            return None
        return result.stdout.decode("utf-8", errors="replace")
    except Exception:
        return None


def get_clipboard_image() -> bytes | None:
    """Return current clipboard image as PNG bytes, or None."""
    try:
        if _WAYLAND:
            result = _run(["wl-paste", "-t", "image/png"])
        else:
            result = _run(["xclip", "-selection", "clipboard", "-t", "image/png", "-o"])
        if result.returncode != 0 or not result.stdout:
            return None
        return result.stdout
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Clipboard Daemon Class
# ---------------------------------------------------------------------------

class ClipboardDaemon:
    def __init__(self):
        ensure_data_dir()
        self.sec = SecurityManager()
        self.history: list[dict] = load_history(self.sec)
        self._last_hash: str | None = None
        self._running = True
        self._lock = threading.Lock()

        # UI instance (resident in memory for instant display)
        self.popup: OpenClipPopup | None = None
        self._has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

        if self._has_display:
            try:
                self.popup = OpenClipPopup(
                    sec=self.sec,
                    history_getter=lambda: self.history,
                    paste_callback=self._on_paste_selected,
                )
            except Exception as exc:
                log.warning("Could not initialize resident UI: %s", exc)

        # Clean stale socket file
        if os.path.exists(SOCKET_PATH):
            try:
                os.unlink(SOCKET_PATH)
            except Exception:
                pass

        # Register signals
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        # Calculate initial hash
        self._last_hash = self._calculate_current_hash()
        log.info("OpenClip daemon initialized (Wayland=%s, Display=%s).", _WAYLAND, self._has_display)

    # ------------------------------------------------------------------
    # Signal & Cleanup
    # ------------------------------------------------------------------

    def _handle_signal(self, signum, frame):
        log.info("Received signal %s – shutting down...", signum)
        self._running = False
        if os.path.exists(SOCKET_PATH):
            try:
                os.unlink(SOCKET_PATH)
            except Exception:
                pass
        if self.popup and self.popup.root:
            try:
                self.popup.root.quit()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Clipboard Polling & Processing
    # ------------------------------------------------------------------

    def _calculate_current_hash(self) -> str | None:
        """Fingerprint current clipboard state."""
        mime_types = get_clipboard_mime_types()
        if not mime_types:
            return None
        raw = get_clipboard_text() or b""
        if isinstance(raw, str):
            raw = raw.encode("utf-8", errors="replace")
        return hashlib.sha256(raw[:4096]).hexdigest()

    def _make_text_entry(self, text: str) -> dict:
        return {
            "type": "text",
            "preview": text[:500],
            "timestamp": time.time(),
        }

    def _make_image_entry(self, png_bytes: bytes, filename: str) -> dict:
        enc_path = self.sec.save_image(png_bytes, filename)
        return {
            "type": "image",
            "enc_path": str(enc_path),
            "timestamp": time.time(),
        }

    def _add_entry(self, entry: dict) -> None:
        """Prepend new entry and maintain MAX_HISTORY limit."""
        with self._lock:
            key = "preview" if entry["type"] == "text" else "enc_path"
            # Deduplicate
            self.history = [h for h in self.history if h.get(key) != entry.get(key)]
            self.history.insert(0, entry)
            self.history = self.history[:MAX_HISTORY]

            self._prune_orphaned_images()
            save_history(self.sec, self.history)

        # Live UI update: if popup is open, prepend card immediately
        if self.popup and self.popup.is_visible:
            try:
                self.popup.root.after(0, self.popup.prepend_card, entry)
            except Exception as exc:
                log.debug("Live update card error: %s", exc)

    def _prune_orphaned_images(self) -> None:
        referenced = {
            h["enc_path"]
            for h in self.history
            if h.get("type") == "image" and "enc_path" in h
        }
        for f in DATA_DIR.glob("*.enc"):
            if f.name == "history.enc":
                continue
            if str(f) not in referenced:
                f.unlink(missing_ok=True)

    def poll_clipboard(self) -> None:
        """Poll clipboard once and record new item if changed."""
        mime_types = get_clipboard_mime_types()
        if not mime_types:
            return

        # Security check
        if is_password_manager_content(mime_types):
            return

        new_hash = self._calculate_current_hash()
        if new_hash == self._last_hash:
            return
        self._last_hash = new_hash

        # Image capture
        if any(m in ("image/png", "image/jpeg", "image/bmp") for m in mime_types):
            png_bytes = get_clipboard_image()
            if png_bytes:
                filename = f"img_{int(time.time() * 1000)}"
                entry = self._make_image_entry(png_bytes, filename)
                self._add_entry(entry)
                log.info("Captured image (%d bytes).", len(png_bytes))
                return

        # Text capture
        text = get_clipboard_text()
        if text and text.strip():
            entry = self._make_text_entry(text)
            self._add_entry(entry)
            log.info("Captured text (%d chars).", len(text))

    def _monitor_loop(self) -> None:
        """Background thread polling clipboard every POLL_INTERVAL."""
        while self._running:
            try:
                self.poll_clipboard()
            except Exception as exc:
                log.debug("Poll error: %s", exc)
            time.sleep(POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Auto-Paste Handling
    # ------------------------------------------------------------------

    def _on_paste_selected(self, entry: dict, target_win_id: int | None):
        """Execute smooth auto-paste back to target window."""
        log.info("Executing auto-paste to window %s", hex(target_win_id) if target_win_id else "None")

        def _do_paste():
            # 1. Reactivate the target window
            if target_win_id:
                x11_helper.activate_window(target_win_id)
            # 2. Wait 60ms for window manager to settle focus
            time.sleep(0.06)
            # 3. Simulate Ctrl+V at cursor
            x11_helper.simulate_paste()

        threading.Thread(target=_do_paste, daemon=True).start()

    # ------------------------------------------------------------------
    # IPC Server (Unix Domain Socket)
    # ------------------------------------------------------------------

    def _start_ipc_server(self):
        """Listen on /tmp/openclip_<uid>.sock for instant show/toggle commands."""
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o600)  # Owner-only access
        server.listen(5)
        server.settimeout(1.0)

        log.info("IPC socket server listening at %s", SOCKET_PATH)

        while self._running:
            try:
                conn, _ = server.accept()
                with conn:
                    conn.settimeout(0.5)
                    data = conn.recv(1024).decode("utf-8", errors="replace").strip()
                    if not data:
                        continue
                    self._handle_ipc_command(data)
            except socket.timeout:
                continue
            except Exception as exc:
                if self._running:
                    log.debug("IPC server exception: %s", exc)

        try:
            server.close()
            if os.path.exists(SOCKET_PATH):
                os.unlink(SOCKET_PATH)
        except Exception:
            pass

    def _handle_ipc_command(self, cmd: str):
        """Process incoming command like 'toggle:0x12345'."""
        log.info("Received IPC command: %s", cmd)

        target_win = None
        if ":" in cmd:
            action, win_str = cmd.split(":", 1)
            try:
                target_win = int(win_str, 0) if win_str and win_str != "0" else None
            except ValueError:
                target_win = None
        else:
            action = cmd

        # Synchronously poll immediately to catch anything copied 1ms ago
        self.poll_clipboard()

        if self.popup:
            if action in ("toggle", "show"):
                self.popup.root.after(0, self.popup.toggle, target_win)
            elif action == "hide":
                self.popup.root.after(0, self.popup.hide)

    # ------------------------------------------------------------------
    # Main Runner
    # ------------------------------------------------------------------

    def run(self):
        # 1. Start clipboard monitor thread
        monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        monitor_thread.start()

        # 2. Start IPC server thread
        ipc_thread = threading.Thread(target=self._start_ipc_server, daemon=True)
        ipc_thread.start()

        log.info("OpenClip daemon is running successfully.")

        # 3. If UI is supported, run Tkinter event loop on main thread
        if self.popup:
            try:
                self.popup.run()
            except Exception as exc:
                log.error("Tkinter mainloop error: %s", exc)
        else:
            # Headless fallback loop
            while self._running:
                time.sleep(1.0)

        # Cleanup socket on exit
        if os.path.exists(SOCKET_PATH):
            try:
                os.unlink(SOCKET_PATH)
            except Exception:
                pass


def main():
    daemon = ClipboardDaemon()
    daemon.run()


if __name__ == "__main__":
    main()

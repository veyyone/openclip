#!/usr/bin/env python3
"""
OpenClip Daemon
Runs silently in the background, monitors the system clipboard,
and stores up to 50 recent entries (text + images) encrypted on disk.

Supports both Wayland (wl-paste) and X11 (xclip).
"""

import io
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Ensure the src/ directory (or install dir) is on the path
sys.path.insert(0, str(Path(__file__).parent))

from security import (
    DATA_DIR,
    SecurityManager,
    ensure_data_dir,
    is_password_manager_content,
    load_history,
    save_history,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
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
POLL_INTERVAL = 0.8  # seconds between clipboard polls

# Determine display server
_WAYLAND = bool(os.environ.get("WAYLAND_DISPLAY"))


# ---------------------------------------------------------------------------
# Clipboard helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        timeout=5,
        **kwargs,
    )


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
    except (FileNotFoundError, subprocess.TimeoutExpired):
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
    except (FileNotFoundError, subprocess.TimeoutExpired):
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
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


# ---------------------------------------------------------------------------
# Daemon main loop
# ---------------------------------------------------------------------------

class ClipboardDaemon:
    def __init__(self):
        ensure_data_dir()
        self.sec = SecurityManager()
        self.history: list[dict] = load_history(self.sec)
        self._last_hash: str | None = self._current_hash()
        self._running = True

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        log.info("OpenClip daemon started (Wayland=%s).", _WAYLAND)

    # ------------------------------------------------------------------

    def _handle_signal(self, signum, frame):
        log.info("Received signal %s – shutting down.", signum)
        self._running = False

    def _current_hash(self) -> str | None:
        """Fast fingerprint of the current clipboard to detect changes."""
        import hashlib
        mime_types = get_clipboard_mime_types()
        if not mime_types:
            return None
        raw = get_clipboard_text() or b""
        if isinstance(raw, str):
            raw = raw.encode()
        return hashlib.sha256(raw[:4096]).hexdigest()

    # ------------------------------------------------------------------

    def _make_text_entry(self, text: str) -> dict:
        return {
            "type": "text",
            "preview": text[:200],
            "timestamp": time.time(),
        }

    def _make_image_entry(self, png_bytes: bytes, filename: str) -> dict:
        enc_path = self.sec.save_image(png_bytes, filename)
        return {
            "type": "image",
            "enc_path": str(enc_path),
            "timestamp": time.time(),
        }

    # ------------------------------------------------------------------

    def _add_entry(self, entry: dict) -> None:
        """Prepend a new entry and trim history to MAX_HISTORY."""
        # Deduplicate: remove existing entry with same preview/path
        key = "preview" if entry["type"] == "text" else "enc_path"
        self.history = [h for h in self.history if h.get(key) != entry.get(key)]
        self.history.insert(0, entry)
        self.history = self.history[:MAX_HISTORY]
        # Clean up orphaned image files
        self._prune_images()
        save_history(self.sec, self.history)

    def _prune_images(self) -> None:
        """Remove encrypted image files that are no longer referenced."""
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

    # ------------------------------------------------------------------

    def _poll(self) -> None:
        """Check for a clipboard change and record it."""
        mime_types = get_clipboard_mime_types()
        if not mime_types:
            return

        # Security: skip password-manager content
        if is_password_manager_content(mime_types):
            log.debug("Skipping password-manager clipboard content.")
            return

        new_hash = self._current_hash()
        if new_hash == self._last_hash:
            return
        self._last_hash = new_hash

        # --- Image ---
        if any(m in ("image/png", "image/jpeg", "image/bmp") for m in mime_types):
            png_bytes = get_clipboard_image()
            if png_bytes:
                filename = f"img_{int(time.time() * 1000)}"
                entry = self._make_image_entry(png_bytes, filename)
                self._add_entry(entry)
                log.info("Captured image (%d bytes).", len(png_bytes))
                return

        # --- Text ---
        text = get_clipboard_text()
        if text and text.strip():
            entry = self._make_text_entry(text)
            self._add_entry(entry)
            log.info("Captured text (%d chars).", len(text))

    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                self._poll()
            except Exception as exc:
                log.error("Poll error: %s", exc, exc_info=True)
            time.sleep(POLL_INTERVAL)
        log.info("OpenClip daemon stopped.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    daemon = ClipboardDaemon()
    daemon.run()


if __name__ == "__main__":
    main()

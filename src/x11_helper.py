"""
OpenClip X11 / Wayland Automation Helper
Handles active window tracking, window reactivation, and synthetic Ctrl+V paste.
Uses native libX11 / libXtst via ctypes on X11 (zero external binary dependencies),
with fallbacks to xdotool / wtype.
"""

import ctypes
from ctypes import c_void_p, c_ulong, c_int, c_uint, byref, POINTER, Structure, c_long, c_char
import logging
import os
import subprocess
import time

log = logging.getLogger(__name__)

_WAYLAND = bool(os.environ.get("WAYLAND_DISPLAY"))

# ---------------------------------------------------------------------------
# X11 Native ctypes Setup
# ---------------------------------------------------------------------------
_x11 = None
_xtst = None

try:
    if not _WAYLAND:
        _x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
        _xtst = ctypes.cdll.LoadLibrary("libXtst.so.6")

        _x11.XOpenDisplay.restype = c_void_p
        _x11.XDefaultRootWindow.restype = c_ulong
        _x11.XInternAtom.restype = c_ulong
        _x11.XKeysymToKeycode.restype = c_uint
        _xtst.XTestFakeKeyEvent.argtypes = [c_void_p, c_uint, c_int, c_ulong]
        _xtst.XTestFakeKeyEvent.restype = c_int
except Exception as exc:
    log.warning("Could not load native libX11/libXtst: %s", exc)


class _XClientMessageData(ctypes.Union):
    _fields_ = [
        ("b", c_char * 20),
        ("s", ctypes.c_short * 10),
        ("l", c_long * 5)
    ]


class _XClientMessageEvent(Structure):
    _fields_ = [
        ("type", c_int),
        ("serial", c_ulong),
        ("send_event", c_int),
        ("display", c_void_p),
        ("window", c_ulong),
        ("message_type", c_ulong),
        ("format", c_int),
        ("data", _XClientMessageData)
    ]


# ---------------------------------------------------------------------------
# Active Window Tracking
# ---------------------------------------------------------------------------

def get_active_window() -> int | None:
    """Return the ID of the currently active window, or None."""
    if _WAYLAND or _x11 is None:
        # On Wayland, try xdotool if Xwayland or return None
        try:
            res = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, timeout=1)
            if res.returncode == 0:
                return int(res.stdout.strip())
        except Exception:
            pass
        return None

    try:
        display = _x11.XOpenDisplay(None)
        if not display:
            return None
        display_ptr = c_void_p(display)
        root = _x11.XDefaultRootWindow(display_ptr)
        atom = _x11.XInternAtom(display_ptr, b"_NET_ACTIVE_WINDOW", False)

        actual_type = c_ulong()
        actual_format = c_int()
        nitems = c_ulong()
        bytes_after = c_ulong()
        prop = c_void_p()

        status = _x11.XGetWindowProperty(
            display_ptr, root, atom, 0, 1, False, 33,
            byref(actual_type), byref(actual_format),
            byref(nitems), byref(bytes_after), byref(prop)
        )
        win_id = None
        if status == 0 and nitems.value > 0 and prop.value:
            win_id = ctypes.cast(prop, POINTER(c_ulong)).contents.value
            _x11.XFree(prop)
        _x11.XCloseDisplay(display_ptr)
        return win_id
    except Exception as exc:
        log.warning("get_active_window error: %s", exc)
        return None


def activate_window(win_id: int | None) -> None:
    """Bring the specified window to front and give it input focus."""
    if not win_id:
        return

    if _WAYLAND:
        try:
            subprocess.run(["xdotool", "windowactivate", "--sync", str(win_id)], timeout=1)
        except Exception:
            pass
        return

    if _x11 is None:
        try:
            subprocess.run(["xdotool", "windowactivate", "--sync", str(win_id)], timeout=1)
        except Exception:
            pass
        return

    try:
        display = _x11.XOpenDisplay(None)
        if not display:
            return
        display_ptr = c_void_p(display)
        root = _x11.XDefaultRootWindow(display_ptr)
        atom = _x11.XInternAtom(display_ptr, b"_NET_ACTIVE_WINDOW", False)

        event = _XClientMessageEvent()
        event.type = 33  # ClientMessage
        event.serial = 0
        event.send_event = 1
        event.display = display_ptr
        event.window = win_id
        event.message_type = atom
        event.format = 32
        event.data.l[0] = 1  # 1 = application
        event.data.l[1] = 0  # timestamp
        event.data.l[2] = 0
        event.data.l[3] = 0
        event.data.l[4] = 0

        mask = (1 << 20) | (1 << 19)  # SubstructureRedirectMask | SubstructureNotifyMask
        _x11.XSendEvent(display_ptr, root, False, c_long(mask), byref(event))
        _x11.XFlush(display_ptr)
        _x11.XCloseDisplay(display_ptr)
    except Exception as exc:
        log.warning("activate_window error: %s", exc)


# ---------------------------------------------------------------------------
# Synthetic Ctrl+V Simulation
# ---------------------------------------------------------------------------

def simulate_paste() -> None:
    """Send synthetic Ctrl+V key combination to the active window."""
    if _WAYLAND:
        # Wayland: use wtype
        try:
            subprocess.run(["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"], timeout=2)
            return
        except Exception:
            pass

    # X11 native via libXtst
    if _x11 is not None and _xtst is not None:
        try:
            display = _x11.XOpenDisplay(None)
            if display:
                d = c_void_p(display)
                ctrl_code = _x11.XKeysymToKeycode(d, 0xffe3)  # XK_Control_L
                v_code = _x11.XKeysymToKeycode(d, 0x0076)     # XK_v

                # Key presses
                _xtst.XTestFakeKeyEvent(d, c_uint(ctrl_code), True, 0)
                _xtst.XTestFakeKeyEvent(d, c_uint(v_code), True, 0)
                # Key releases
                _xtst.XTestFakeKeyEvent(d, c_uint(v_code), False, 0)
                _xtst.XTestFakeKeyEvent(d, c_uint(ctrl_code), False, 0)
                _x11.XFlush(d)
                _x11.XCloseDisplay(d)
                return
        except Exception as exc:
            log.warning("Native XTestFakeKeyEvent failed: %s", exc)

    # Fallback to xdotool
    try:
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], timeout=2)
    except Exception as exc:
        log.warning("xdotool fallback failed: %s", exc)


# ---------------------------------------------------------------------------
# Clipboard Copy Helper
# ---------------------------------------------------------------------------

def copy_to_system_clipboard(text: str | None = None, image_bytes: bytes | None = None) -> bool:
    """Write text or image to system clipboard."""
    try:
        if text is not None:
            if _WAYLAND:
                proc = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
            else:
                proc = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
            proc.communicate(input=text.encode("utf-8"), timeout=2)
            return proc.returncode == 0

        elif image_bytes is not None:
            if _WAYLAND:
                proc = subprocess.Popen(["wl-copy", "--type", "image/png"], stdin=subprocess.PIPE)
            else:
                proc = subprocess.Popen(["xclip", "-selection", "clipboard", "-t", "image/png"], stdin=subprocess.PIPE)
            proc.communicate(input=image_bytes, timeout=2)
            return proc.returncode == 0
    except Exception as exc:
        log.error("Failed to copy to clipboard: %s", exc)
    return False

# Project Specification: OpenClip (Secure Ubuntu Clipboard Manager)

## Goal
Build a secure, open-source, Windows-style (`Win + V`) clipboard manager for Ubuntu Linux (supporting both Wayland and X11) and package it as an easy-to-install `.deb` file for public release on GitHub.

## Core Features
1. **Background Monitor (`src/daemon.py`)**:
   - Runs silently in the background on user login.
   - Automatically captures copied text (`text/plain`) and screenshots/images (`image/png`) using `wl-paste` (Wayland) or `xclip` (X11).
   - Deduplicates items and stores up to 30–50 recent entries.
2. **Popup UI (`src/ui.py`)**:
   - Triggered globally via `Super + V`.
   - Dark-themed, always-on-top scrollable popup showing text previews and image thumbnails (using Tkinter + Pillow).
   - **Single-click auto-paste**: Clicking any item copies it back to the system clipboard, closes the popup, returns focus to the previous app, and automatically triggers `Ctrl + V` (using `wtype` on Wayland or `xdotool` on X11).
   - Closes on `Escape`.
3. **Security (`src/security.py`)**:
   - **Encryption at rest**: Encrypt both the history JSON (`history.enc`) and saved PNGs (`*.png.enc`) using `cryptography.fernet.Fernet`.
   - **Key management**: Store the Fernet encryption key securely in Ubuntu's native GNOME Keyring via the `keyring` library.
   - **File permissions**: Store data in `~/.local/share/openclip/` with strict owner-only permissions (`chmod 700` for dirs, `chmod 600` for files).
   - **Password-manager filtering**: Skip recording any clipboard content that includes `x-kde-passwordManagerHint` or `application/x-keepassxc-secret` MIME types.
   - **100% offline**: Zero telemetry or network calls.
4. **Packaging & Distribution (`build_deb.sh`)**:
   - Build a `.deb` package using `dpkg-deb` that installs `/usr/lib/openclip/`, `/usr/bin/openclip`, `/usr/bin/openclip-daemon`, and `/etc/xdg/autostart/openclip.desktop`.
   - Declare all Ubuntu `Depends` (`python3`, `python3-tk`, `python3-pil`, `python3-pil.imagetk`, `python3-cryptography`, `python3-keyring`, `xclip`, `wl-clipboard`, `xdotool`, `wtype`).
   - Include a helper command or post-install setup using `gsettings` to bind `Super + V` to `/usr/bin/openclip`.

## Tasks for the Agent
1. Scaffold the full repository (`src/daemon.py`, `src/ui.py`, `src/security.py`, `build_deb.sh`, `README.md`, `LICENSE`, `SECURITY.md`).
2. Wire `daemon.py` and `ui.py` to use `security.py` for encrypted text/image storage and password-manager filtering.
3. Verify the `.deb` build succeeds cleanly with `./build_deb.sh`.


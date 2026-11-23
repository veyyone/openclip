# 📋 OpenClip

**A secure, open-source, Windows-style (`Super + V`) clipboard manager for Ubuntu Linux.**

OpenClip records every piece of text and every screenshot you copy, stores them encrypted on disk, and lets you pick any past entry from a slick dark popup — with one click to auto-paste.

---

## ✨ Features

| Feature | Detail |
|---|---|
| **Windows-style popup** | Press `Super + V` to open the clipboard history panel |
| **Text + Image history** | Captures `text/plain` and `image/png` from the clipboard |
| **Single-click auto-paste** | Selects an item, copies it, closes the popup, and fires `Ctrl + V` |
| **AES encryption at rest** | All entries encrypted with Fernet (AES-128-CBC + HMAC-SHA256) |
| **GNOME Keyring integration** | Encryption key stored in the system keyring (never on disk in plaintext) |
| **Password-manager safe** | Skips KeePassXC and KDE Wallet clipboard entries |
| **Wayland & X11** | Works with both display servers |
| **100% offline** | Zero telemetry, zero network calls |
| **Easy install** | Single `.deb` package |

---

## 📦 Installation

### From the `.deb` package (recommended)

```bash
# Download the latest release
wget https://github.com/example/openclip/releases/latest/download/openclip_1.0.0_all.deb

# Install (automatically installs dependencies)
sudo apt install ./openclip_1.0.0_all.deb
```

The installer will attempt to register `Super + V` as a system keyboard shortcut.  
**Log out and back in** for the autostart daemon and shortcut to take effect.

### From source

```bash
git clone https://github.com/example/openclip.git
cd openclip

# Install Python dependencies
pip3 install cryptography keyring pillow

# Install system tools (Wayland)
sudo apt install wl-clipboard wtype

# Or for X11
sudo apt install xclip xdotool

# Start the daemon
python3 src/daemon.py &

# Open the UI
python3 src/ui.py
```

---

## 🔧 Usage

| Action | How |
|---|---|
| Open clipboard history | Press `Super + V` |
| Paste an item | Click it |
| Close without pasting | Press `Escape` or click outside |

---

## 🏗️ Architecture

```
~/.local/share/openclip/
├── history.enc          ← Fernet-encrypted JSON clipboard history
├── img_*.png.enc        ← Fernet-encrypted PNG images
└── daemon.log           ← Background daemon log

/usr/lib/openclip/
├── daemon.py            ← Background clipboard monitor
├── ui.py                ← Tkinter popup UI
└── security.py          ← Encryption & key management
```

### Display-server detection

| Environment variable | Tool used |
|---|---|
| `WAYLAND_DISPLAY` set | `wl-paste` / `wl-copy` / `wtype` |
| (X11 fallback) | `xclip` / `xdotool` |

---

## 🔐 Security

See [SECURITY.md](SECURITY.md) for the full security model.

**TL;DR:**
- Data encrypted with **Fernet** (AES-128-CBC + HMAC-SHA256)
- Key stored in **GNOME Keyring** (falls back to a `chmod 600` key file)
- Data directory: `~/.local/share/openclip/` with `700` permissions, files `600`
- Password-manager content (`x-kde-passwordManagerHint`, `application/x-keepassxc-secret`) is **never recorded**

---

## 🛠️ Building the `.deb` package

```bash
./build_deb.sh
# → build/openclip_1.0.0_all.deb
```

Requires `dpkg-deb` (available on any Debian/Ubuntu system).

---

## 📋 System Requirements

- Ubuntu 22.04 LTS or later (or any Debian-based distro)
- Python 3.10+
- GNOME desktop (for `Super + V` shortcut auto-registration)
- Wayland or X11

---

## 📜 License

[MIT](LICENSE) © 2024 OpenClip Contributors

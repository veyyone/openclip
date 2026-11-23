#!/usr/bin/env bash
# build_deb.sh – Build the OpenClip .deb package
# Usage: ./build_deb.sh
set -euo pipefail

PACKAGE="openclip"
VERSION="1.0.0"
ARCH="all"
BUILD_DIR="$(pwd)/build/${PACKAGE}_${VERSION}_${ARCH}"

echo "==> Cleaning previous build..."
rm -rf "$(pwd)/build"

# ---------------------------------------------------------------------------
# 1. Directory layout
# ---------------------------------------------------------------------------
echo "==> Creating package directory layout..."

# Application library
install -d "${BUILD_DIR}/usr/lib/openclip"

# Executables
install -d "${BUILD_DIR}/usr/bin"

# XDG autostart
install -d "${BUILD_DIR}/etc/xdg/autostart"

# Desktop application entry
install -d "${BUILD_DIR}/usr/share/applications"

# Man page directory
install -d "${BUILD_DIR}/usr/share/man/man1"

# DEBIAN control directory
install -d "${BUILD_DIR}/DEBIAN"

# ---------------------------------------------------------------------------
# 2. Install application source
# ---------------------------------------------------------------------------
echo "==> Installing application files..."
install -m 644 src/daemon.py    "${BUILD_DIR}/usr/lib/openclip/daemon.py"
install -m 644 src/ui.py        "${BUILD_DIR}/usr/lib/openclip/ui.py"
install -m 644 src/security.py  "${BUILD_DIR}/usr/lib/openclip/security.py"

# ---------------------------------------------------------------------------
# 3. Launcher scripts (wrapping the Python modules)
# ---------------------------------------------------------------------------
echo "==> Creating launcher scripts..."

cat > "${BUILD_DIR}/usr/bin/openclip" << 'SCRIPT'
#!/bin/sh
exec python3 /usr/lib/openclip/ui.py "$@"
SCRIPT
chmod 755 "${BUILD_DIR}/usr/bin/openclip"

cat > "${BUILD_DIR}/usr/bin/openclip-daemon" << 'SCRIPT'
#!/bin/sh
exec python3 /usr/lib/openclip/daemon.py "$@"
SCRIPT
chmod 755 "${BUILD_DIR}/usr/bin/openclip-daemon"

# ---------------------------------------------------------------------------
# 4. XDG Autostart entry (starts daemon on login)
# ---------------------------------------------------------------------------
echo "==> Installing autostart entry..."
cat > "${BUILD_DIR}/etc/xdg/autostart/openclip.desktop" << 'DESKTOP'
[Desktop Entry]
Type=Application
Name=OpenClip Daemon
Comment=Secure clipboard history manager daemon
Exec=openclip-daemon
Icon=edit-paste
Terminal=false
NoDisplay=true
Categories=Utility;
X-GNOME-Autostart-enabled=true
DESKTOP

# ---------------------------------------------------------------------------
# 5. .desktop application entry (for launcher / Alt+F2)
# ---------------------------------------------------------------------------
cat > "${BUILD_DIR}/usr/share/applications/openclip.desktop" << 'DESKTOP'
[Desktop Entry]
Version=1.0
Type=Application
Name=OpenClip
GenericName=Clipboard Manager
Comment=Secure Windows-style clipboard history (Super+V)
Exec=openclip
Icon=edit-paste
Terminal=false
StartupNotify=false
Categories=Utility;GTK;
Keywords=clipboard;history;copy;paste;
DESKTOP

# ---------------------------------------------------------------------------
# 6. DEBIAN/control
# ---------------------------------------------------------------------------
echo "==> Writing DEBIAN/control..."
cat > "${BUILD_DIR}/DEBIAN/control" << CONTROL
Package: ${PACKAGE}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Depends: python3 (>= 3.10),
 python3-tk,
 python3-pil,
 python3-pil.imagetk,
 python3-cryptography,
 python3-keyring,
 xclip,
 wl-clipboard,
 xdotool,
 wtype
Maintainer: OpenClip Contributors <openclip@example.com>
Homepage: https://github.com/example/openclip
Description: Secure Windows-style clipboard manager for Ubuntu
 OpenClip records clipboard history (text and images) in an AES-encrypted
 local store. A Super+V popup lets you browse and paste any recent entry
 with a single click.
 .
 Supports both Wayland (wl-paste / wl-copy / wtype) and X11 (xclip / xdotool).
 Clipboard data is encrypted with Fernet (AES-128-CBC + HMAC-SHA256) and the
 encryption key is stored in the GNOME Keyring.
 .
 100% offline. No telemetry. Password-manager content is never recorded.
CONTROL

# ---------------------------------------------------------------------------
# 7. DEBIAN/postinst – bind Super+V shortcut via gsettings
# ---------------------------------------------------------------------------
echo "==> Writing DEBIAN/postinst..."
cat > "${BUILD_DIR}/DEBIAN/postinst" << 'POSTINST'
#!/bin/sh
set -e

# Bind Super+V to openclip for the installing user (best-effort)
# gsettings requires a D-Bus session; skip gracefully if unavailable.
if command -v gsettings > /dev/null 2>&1 && [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
    SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
    KEY="custom-keybindings"

    EXISTING=$(gsettings get ${SCHEMA} ${KEY} 2>/dev/null || echo "@as []")
    NEW_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/openclip/"

    # Only add if not already present
    if ! echo "${EXISTING}" | grep -q "openclip"; then
        CUSTOM_SCHEMA="${SCHEMA}.custom-keybinding:${NEW_PATH}"
        gsettings set "${CUSTOM_SCHEMA}" name    "OpenClip" 2>/dev/null || true
        gsettings set "${CUSTOM_SCHEMA}" command "openclip" 2>/dev/null || true
        gsettings set "${CUSTOM_SCHEMA}" binding "<Super>v" 2>/dev/null || true

        # Append to the list
        NEW_LIST=$(echo "${EXISTING}" | sed "s|]|, '${NEW_PATH}']|" | sed "s|\[\s*, |[|")
        gsettings set "${SCHEMA}" "${KEY}" "${NEW_LIST}" 2>/dev/null || true
        echo "OpenClip: Super+V shortcut registered."
    fi
fi

echo "OpenClip installed. Log out and back in, then press Super+V."
exit 0
POSTINST
chmod 755 "${BUILD_DIR}/DEBIAN/postinst"

# ---------------------------------------------------------------------------
# 8. DEBIAN/prerm – remove Super+V shortcut
# ---------------------------------------------------------------------------
cat > "${BUILD_DIR}/DEBIAN/prerm" << 'PRERM'
#!/bin/sh
set -e

if command -v gsettings > /dev/null 2>&1 && [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
    SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
    NEW_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/openclip/"
    EXISTING=$(gsettings get ${SCHEMA} custom-keybindings 2>/dev/null || echo "@as []")

    if echo "${EXISTING}" | grep -q "openclip"; then
        NEW_LIST=$(echo "${EXISTING}" | sed "s|, '${NEW_PATH}'||" | sed "s|'${NEW_PATH}', ||" | sed "s|'${NEW_PATH}'||")
        gsettings set "${SCHEMA}" custom-keybindings "${NEW_LIST}" 2>/dev/null || true
    fi
fi

exit 0
PRERM
chmod 755 "${BUILD_DIR}/DEBIAN/prerm"

# ---------------------------------------------------------------------------
# 9. DEBIAN/copyright (brief)
# ---------------------------------------------------------------------------
cat > "${BUILD_DIR}/DEBIAN/copyright" << 'COPYRIGHT'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: openclip
Source: https://github.com/example/openclip

Files: *
Copyright: 2024 OpenClip Contributors
License: MIT
COPYRIGHT

# ---------------------------------------------------------------------------
# 10. Build the package
# ---------------------------------------------------------------------------
echo "==> Building .deb package..."
dpkg-deb --build --root-owner-group "${BUILD_DIR}"

DEB_FILE="$(pwd)/build/${PACKAGE}_${VERSION}_${ARCH}.deb"
echo ""
echo "✅  Package built successfully: ${DEB_FILE}"
echo ""
echo "Install with:"
echo "  sudo apt install ./build/${PACKAGE}_${VERSION}_${ARCH}.deb"
echo ""
echo "Or inspect with:"
echo "  dpkg-deb -I ./build/${PACKAGE}_${VERSION}_${ARCH}.deb"

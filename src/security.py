"""
OpenClip Security Module
Handles AES encryption via Fernet, key storage in GNOME Keyring,
file permissions, and password-manager MIME-type filtering.
"""

import os
import stat
import logging
from pathlib import Path

# ---------------------------------------------------------------------------
# Optional imports – fail gracefully so unit tests / CI can still import us
# ---------------------------------------------------------------------------
try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None  # type: ignore

try:
    import keyring
except ImportError:
    keyring = None  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
KEYRING_SERVICE = "openclip"
KEYRING_USERNAME = "fernet-key"
DATA_DIR = Path.home() / ".local" / "share" / "openclip"
HISTORY_FILE = DATA_DIR / "history.enc"

# MIME types that indicate a password-manager secret – skip these entries
_PM_MIME_BLOCKLIST = {
    "x-kde-passwordmanagerhint",  # lowercased for comparison
    "application/x-keepassxc-secret",
}

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data directory setup
# ---------------------------------------------------------------------------

def ensure_data_dir() -> Path:
    """
    Create ~/.local/share/openclip/ with strict owner-only permissions
    (chmod 700 for directories, chmod 600 for files).
    Returns the data directory path.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA_DIR, stat.S_IRWXU)  # 700
    return DATA_DIR


def secure_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* then set permissions to 600 (owner r/w only)."""
    ensure_data_dir()
    path.write_bytes(data)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 600


# ---------------------------------------------------------------------------
# Key management via GNOME Keyring
# ---------------------------------------------------------------------------

def _load_or_create_key() -> bytes:
    """
    Load the Fernet key from the GNOME Keyring.
    If no key is stored yet, generate one and persist it.
    Falls back to a local key file when the keyring is unavailable.
    """
    if keyring is not None:
        try:
            stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
            if stored:
                return stored.encode()
            key = Fernet.generate_key()
            keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key.decode())
            log.info("Generated and stored new Fernet key in GNOME Keyring.")
            return key
        except Exception as exc:
            log.warning("GNOME Keyring unavailable (%s); falling back to key file.", exc)

    # Fallback: store key in a 600-permission file inside the data dir
    key_file = DATA_DIR / ".key"
    ensure_data_dir()
    if key_file.exists():
        return key_file.read_bytes().strip()
    key = Fernet.generate_key()
    secure_write(key_file, key)
    log.info("Generated and stored new Fernet key in key file.")
    return key


# ---------------------------------------------------------------------------
# Encryption / decryption helpers
# ---------------------------------------------------------------------------

class SecurityManager:
    """Central security facade used by daemon and UI."""

    def __init__(self):
        ensure_data_dir()
        if Fernet is None:
            raise RuntimeError(
                "The 'cryptography' package is not installed. "
                "Install it with: pip3 install cryptography"
            )
        key = _load_or_create_key()
        self._fernet = Fernet(key)

    # --- text ---

    def encrypt_text(self, plaintext: str) -> bytes:
        """Encrypt a UTF-8 string and return the ciphertext bytes."""
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt_text(self, ciphertext: bytes) -> str:
        """Decrypt ciphertext bytes to a UTF-8 string."""
        return self._fernet.decrypt(ciphertext).decode("utf-8")

    # --- binary (images) ---

    def encrypt_bytes(self, data: bytes) -> bytes:
        """Encrypt raw bytes (e.g. PNG image data)."""
        return self._fernet.encrypt(data)

    def decrypt_bytes(self, ciphertext: bytes) -> bytes:
        """Decrypt ciphertext bytes back to raw bytes."""
        return self._fernet.decrypt(ciphertext)

    # --- image files ---

    def save_image(self, image_data: bytes, filename: str) -> Path:
        """
        Encrypt *image_data* (PNG bytes) and write to DATA_DIR/<filename>.enc.
        Returns the path of the encrypted file.
        """
        enc_path = DATA_DIR / f"{filename}.enc"
        secure_write(enc_path, self.encrypt_bytes(image_data))
        return enc_path

    def load_image(self, enc_path: Path) -> bytes:
        """Decrypt and return raw image bytes from an .enc file."""
        return self.decrypt_bytes(enc_path.read_bytes())

    def delete_image(self, enc_path: Path) -> None:
        """Remove an encrypted image file if it exists."""
        try:
            enc_path.unlink(missing_ok=True)
        except Exception as exc:
            log.warning("Could not delete image %s: %s", enc_path, exc)


# ---------------------------------------------------------------------------
# History persistence (JSON encrypted)
# ---------------------------------------------------------------------------

def load_history(sec: SecurityManager) -> list:
    """
    Load and decrypt the clipboard history from HISTORY_FILE.
    Returns an empty list if the file does not exist or cannot be decrypted.
    """
    import json
    if not HISTORY_FILE.exists():
        return []
    try:
        raw = HISTORY_FILE.read_bytes()
        plaintext = sec.decrypt_text(raw)
        return json.loads(plaintext)
    except Exception as exc:
        log.error("Failed to load history: %s", exc)
        return []


def save_history(sec: SecurityManager, history: list) -> None:
    """Encrypt and persist the clipboard history list to HISTORY_FILE."""
    import json
    plaintext = json.dumps(history, ensure_ascii=False)
    ciphertext = sec.encrypt_text(plaintext)
    secure_write(HISTORY_FILE, ciphertext)


# ---------------------------------------------------------------------------
# Password-manager filtering
# ---------------------------------------------------------------------------

def is_password_manager_content(mime_types: list[str]) -> bool:
    """
    Return True if *mime_types* contains any MIME type that indicates
    the clipboard content originates from a password manager and should
    not be recorded.
    """
    lowered = {m.lower() for m in mime_types}
    return bool(lowered & _PM_MIME_BLOCKLIST)

# Security Model — OpenClip

## Threat model

OpenClip is designed to protect clipboard history from:

- **Other local users** on the same machine (file permissions)
- **Passive disk reads** (encryption at rest)
- **Accidental leakage of secrets** (password-manager filtering)

It does **not** protect against:

- A malicious process running as the same user (inherent clipboard limitation)
- An attacker with root access

---

## Encryption

| Property | Value |
|---|---|
| Algorithm | Fernet = AES-128-CBC + HMAC-SHA256 |
| Library | `cryptography` (Python) |
| Encrypted files | `~/.local/share/openclip/history.enc`, `*.png.enc` |
| Key length | 256 bits (Fernet URL-safe base64 encoded) |

Fernet guarantees **authenticated encryption**: an attacker who modifies a ciphertext file on disk will get a `cryptography.fernet.InvalidToken` exception, not corrupt plaintext.

---

## Key management

1. **Primary**: Key is stored in the **GNOME Keyring** via the `keyring` Python library.  
   Service: `openclip`, username: `fernet-key`.
2. **Fallback** (headless / no keyring): Key is written to `~/.local/share/openclip/.key` with `chmod 600` (owner read/write only).

A new key is generated automatically on first run with `Fernet.generate_key()`.

---

## File permissions

| Path | Mode | Meaning |
|---|---|---|
| `~/.local/share/openclip/` | `700` | Only owner can list/enter |
| `history.enc` | `600` | Owner read/write only |
| `*.png.enc` | `600` | Owner read/write only |
| `.key` (fallback) | `600` | Owner read/write only |

---

## Password-manager filtering

OpenClip inspects the MIME types advertised by the clipboard **before** reading the content. If any of the following types are present, the entry is **silently skipped** and never stored:

| MIME type | Source |
|---|---|
| `x-kde-passwordManagerHint` | KDE Wallet, KeePass on KDE |
| `application/x-keepassxc-secret` | KeePassXC |

---

## History limits

- Maximum **50 entries** are retained.
- Older entries are dropped; their encrypted image files are deleted from disk.
- Deduplication prevents the same content being stored twice.

---

## Offline guarantee

OpenClip contains **zero network calls**. It does not phone home, check for updates, or send telemetry. All data stays on the local machine.

---

## Reporting a vulnerability

Please open a **private** GitHub Security Advisory at:  
`https://github.com/example/openclip/security/advisories/new`

Do **not** open a public issue for security vulnerabilities.

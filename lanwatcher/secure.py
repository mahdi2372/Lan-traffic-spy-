"""Optional protection of sensitive metadata (router credentials, user notes).

Windows: DPAPI (CryptProtectData) bound to the current user - no passphrase.
Other platforms: AES-256-GCM with a random key stored beside the database with
0600 permissions. Failures degrade to 'not available'; plaintext is never
written when encryption is requested but impossible.
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
from pathlib import Path
from typing import Optional

from .util import is_windows

log = logging.getLogger("lanwatcher.secure")

_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SecureStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self._aes_key: Optional[bytes] = None

    # -------------------------------------------------- public

    def available(self) -> bool:
        return is_windows() or self._load_aes_key() is not None

    def protect(self, plaintext: str) -> Optional[str]:
        if plaintext is None:
            return None
        data = plaintext.encode("utf-8")
        if is_windows():
            return "dpapi:" + self._dpapi_protect(data).hex()
        key = self._load_aes_key()
        if key is None:
            return None
        return "aes:" + self._aes_gcm_protect(key, data).hex()

    def unprotect(self, token: Optional[str]) -> Optional[str]:
        if token is None:
            return None
        if ":" not in token:
            return token  # legacy/plain value
        scheme, blob = token.split(":", 1)
        try:
            if scheme == "dpapi":
                if not is_windows():
                    return None
                return self._dpapi_unprotect(bytes.fromhex(blob)).decode("utf-8")
            if scheme == "aes":
                key = self._load_aes_key()
                if key is None:
                    return None
                return self._aes_gcm_unprotect(key, bytes.fromhex(blob)).decode("utf-8")
        except Exception:  # noqa: BLE001
            log.exception("failed to unprotect value")
            return None
        return None

    def wipe(self) -> None:
        """Remove locally stored keys (used by 'delete history & keys')."""
        self._aes_key = None
        kfile = self.data_dir / "secret.key"
        try:
            kfile.unlink(missing_ok=True)
        except OSError:
            pass

    # -------------------------------------------------- AES-GCM (non-Windows)

    def _load_aes_key(self) -> Optional[bytes]:
        if self._aes_key is not None:
            return self._aes_key
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        except ImportError:
            return None
        kfile = self.data_dir / "secret.key"
        try:
            if kfile.exists():
                key = bytes.fromhex(kfile.read_text(encoding="utf-8").strip())
            else:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _A

                key = _A.generate_key(bit_length=256)
                self.data_dir.mkdir(parents=True, exist_ok=True)
                kfile.write_text(key.hex(), encoding="utf-8")
                os.chmod(kfile, 0o600)
            if len(key) != 32:
                return None
            self._aes_key = key
            return key
        except OSError:
            log.exception("cannot read/write secret key")
            return None

    @staticmethod
    def _aes_gcm_protect(key: bytes, data: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(12)
        return nonce + AESGCM(key).encrypt(nonce, data, None)

    @staticmethod
    def _aes_gcm_unprotect(key: bytes, blob: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(key).decrypt(blob[:12], blob[12:], None)

    # -------------------------------------------------- DPAPI (Windows)

    @staticmethod
    def _dpapi_protect(data: bytes) -> bytes:
        return _dpapi_call(data, protect=True)

    @staticmethod
    def _dpapi_unprotect(blob: bytes) -> bytes:
        return _dpapi_call(blob, protect=False)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_call(data: bytes, protect: bool) -> bytes:  # pragma: no cover - windows only
    if not is_windows():
        raise RuntimeError("DPAPI is only available on Windows")
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    blob_in = _DataBlob(len(data), ctypes.cast(ctypes.c_char_p(data), ctypes.POINTER(ctypes.c_byte)))
    blob_out = _DataBlob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)  # type: ignore[attr-defined]

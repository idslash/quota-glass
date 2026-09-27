from __future__ import annotations

import ctypes
import json
import os
import tempfile
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", ctypes.c_ulonglong),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_advapi = ctypes.WinDLL("Advapi32.dll") if os.name == "nt" else None
if _advapi:
    _advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    _advapi.CredReadW.restype = wintypes.BOOL
    _advapi.CredFree.argtypes = [ctypes.c_void_p]
    _advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    _advapi.CredWriteW.restype = wintypes.BOOL
    _advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _advapi.CredDeleteW.restype = wintypes.BOOL


class WindowsCredentialStore:
    """Small wrapper around Credential Manager; secret bytes never touch config/cache."""

    def read(self, target: str) -> str | None:
        if not _advapi:
            return None
        pointer = ctypes.c_void_p()
        if not _advapi.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            return None
        try:
            credential = ctypes.cast(pointer, ctypes.POINTER(CREDENTIAL)).contents
            size = int(credential.CredentialBlobSize)
            if size <= 0:
                return None
            raw = ctypes.string_at(credential.CredentialBlob, size)
            for encoding in ("utf-8", "utf-16-le"):
                try:
                    value = raw.decode(encoding).rstrip("\x00")
                    if value:
                        return value
                except UnicodeDecodeError:
                    continue
            return None
        finally:
            _advapi.CredFree(pointer)

    def write(self, target: str, secret: str, username: str = "LimitBar") -> None:
        if not _advapi:
            raise OSError("Windows Credential Manager is unavailable")
        raw = secret.encode("utf-16-le")
        buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = CREDENTIAL()
        credential.Type = CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.UserName = username
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE
        credential.CredentialBlobSize = len(raw)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        if not _advapi.CredWriteW(ctypes.byref(credential), 0):
            raise ctypes.WinError()

    def delete(self, target: str) -> None:
        if _advapi and not _advapi.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
            error = ctypes.get_last_error()
            if error not in (0, 1168):
                raise ctypes.WinError(error)


def read_claude_oauth_token(store: WindowsCredentialStore | None = None) -> str | None:
    """Reuse Claude Code's login; do not copy it into LimitBar storage."""
    store = store or WindowsCredentialStore()
    candidates: list[str] = []
    credential = store.read("Claude Code-credentials")
    if credential:
        candidates.append(credential)

    user_profile = os.environ.get("USERPROFILE")
    config_root = os.environ.get("CLAUDE_CONFIG_DIR")
    paths = []
    if config_root:
        paths.append(Path(config_root) / ".credentials.json")
    if user_profile:
        paths.append(Path(user_profile) / ".claude" / ".credentials.json")
    paths.append(Path.home() / ".claude" / ".credentials.json")
    for path in dict.fromkeys(paths):
        try:
            if path.is_file():
                candidates.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue

    for value in candidates:
        token = _extract_claude_token(value)
        if token:
            return token
    return None


@dataclass(slots=True)
class ClaudeOAuthCredentials:
    """Refreshable Claude credentials plus their original secure location."""

    root: dict
    access_token: str
    refresh_token: str | None
    expires_at_ms: int | None
    path: Path | None = None
    credential_target: str | None = None

    def persist_rotated(self, access_token: str, refresh_token: str | None, expires_at_ms: int) -> bool:
        latest_raw: str | None = None
        store = WindowsCredentialStore()
        if self.credential_target:
            latest_raw = store.read(self.credential_target)
        elif self.path:
            try:
                latest_raw = self.path.read_text(encoding="utf-8")
            except OSError:
                return False
        try:
            latest = json.loads(latest_raw or "{}")
        except json.JSONDecodeError:
            return False
        latest_oauth = latest.get("claudeAiOauth") or latest.get("oauth") or latest
        if not isinstance(latest_oauth, dict) or latest_oauth.get("accessToken") != self.access_token:
            # Claude Code rotated the credentials concurrently; never overwrite it.
            return False
        updated_oauth = dict(latest_oauth)
        updated_oauth["accessToken"] = access_token
        if refresh_token:
            updated_oauth["refreshToken"] = refresh_token
        updated_oauth["expiresAt"] = expires_at_ms
        updated = dict(latest)
        if "claudeAiOauth" in latest:
            updated["claudeAiOauth"] = updated_oauth
        elif "oauth" in latest:
            updated["oauth"] = updated_oauth
        else:
            updated = updated_oauth
        serialized = json.dumps(updated, separators=(",", ":"))
        if self.credential_target:
            store.write(self.credential_target, serialized, "Claude Code")
            return True
        if not self.path:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            return True
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def read_claude_oauth_credentials(store: WindowsCredentialStore | None = None) -> ClaudeOAuthCredentials | None:
    store = store or WindowsCredentialStore()
    candidates: list[tuple[str, Path | None, str | None]] = []
    credential = store.read("Claude Code-credentials")
    if credential:
        candidates.append((credential, None, "Claude Code-credentials"))
    user_profile = os.environ.get("USERPROFILE")
    config_root = os.environ.get("CLAUDE_CONFIG_DIR")
    paths: list[Path] = []
    if config_root:
        paths.append(Path(config_root) / ".credentials.json")
    if user_profile:
        paths.append(Path(user_profile) / ".claude" / ".credentials.json")
    paths.append(Path.home() / ".claude" / ".credentials.json")
    for path in dict.fromkeys(paths):
        try:
            if path.is_file():
                candidates.append((path.read_text(encoding="utf-8"), path, None))
        except OSError:
            continue
    for raw, path, target in candidates:
        try:
            root = json.loads(raw.strip().strip("\x00"))
        except json.JSONDecodeError:
            continue
        oauth = root.get("claudeAiOauth") or root.get("oauth") or root
        if not isinstance(oauth, dict):
            continue
        access = oauth.get("accessToken") or oauth.get("access_token")
        if not isinstance(access, str) or not access:
            continue
        refresh = oauth.get("refreshToken") or oauth.get("refresh_token")
        expires = oauth.get("expiresAt") or oauth.get("expires_at")
        return ClaudeOAuthCredentials(
            root=root,
            access_token=access,
            refresh_token=refresh if isinstance(refresh, str) and refresh else None,
            expires_at_ms=int(expires) if isinstance(expires, (int, float)) else None,
            path=path,
            credential_target=target,
        )
    return None


def _extract_claude_token(value: str) -> str | None:
    stripped = value.strip().strip("\x00")
    if stripped.startswith("sk-ant-oat"):
        return stripped
    try:
        root = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    oauth = root.get("claudeAiOauth") or root.get("oauth") or root
    if isinstance(oauth, dict):
        token = oauth.get("accessToken") or oauth.get("access_token")
        if isinstance(token, str) and token.strip():
            return token.strip()
    return None

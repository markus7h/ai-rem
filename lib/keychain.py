"""Geräte-Token im OS-Keychain statt in Klartextdateien.

Pro Workstation hält ai-rem nur noch EIN Secret: den Geräte-Token. Dieses Modul
kapselt die Ablage plattformabhängig (macOS Keychain, libsecret, Windows
Credential Manager, Datei-Fallback) hinter get()/set()/delete().

Wird von bin/ai-rem, den Hooks (~/.claude/hooks/*.py) und scripts/setup.py per
importlib geladen → reine stdlib, Python 3.8+ (Workstation hat 3.9.6).

Backends sind Klassen mit injizierbaren Abhängigkeiten (run/which/adv), damit
die Tests ohne echte Keychains laufen; die Modul-Funktionen sind bewusst flach,
damit Aufrufer sie trivial monkeypatchen können.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from typing import Callable, Optional

SERVICE = "ai-rem"
# Account überschreibbar, damit Selbsttests nicht den echten Token anfassen.
ACCOUNT = os.environ.get("AI_REM_KEYCHAIN_ACCOUNT", "device-token")
# Reißleine für security/secret-tool: normal antworten beide in Millisekunden.
TIMEOUT_S = 15


class KeychainError(RuntimeError):
    """Backend-Fehler außer „nicht gefunden“ (das liefert get() als "")."""


# --------------------------------------------------------------------------- macOS

class _MacBackend:
    """`security` im interaktiven Modus (-i) mit dem Kommando auf stdin, damit das
    Secret nie in argv landet und damit nie in `ps`/Prozesslisten auftaucht.

    Items, die via `security` angelegt wurden, liest `security` später ohne
    GUI-Prompt (das CLI steht in der ACL des Items). Bei gesperrtem
    Login-Keychain (z. B. SSH-Session ohne GUI-Login) ist vorher
    `security unlock-keychain` nötig — sonst hängt/fehlt der Zugriff."""

    name = "macOS Keychain"
    _NOT_FOUND_RC = 44  # errSecItemNotFound (-25300)

    def __init__(self, account: str, run: Callable = subprocess.run):
        self.account = account
        self.run = run

    @staticmethod
    def _quote(value: str) -> str:
        # security -i tokenisiert shell-ähnlich: nur " und \ sind in "…" speziell.
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _security(self, cmd: str):
        # ponytail: ohne Timeout bliebe der synchrone SessionStart-Hook bei
        # gesperrtem Keychain (SSH ohne GUI) am Unlock-Prompt hängen.
        return self.run(["security", "-i"], input=cmd + "\n",
                        capture_output=True, text=True, timeout=TIMEOUT_S)

    def get(self) -> str:
        p = self._security("find-generic-password -a %s -s %s -w"
                           % (self._quote(self.account), SERVICE))
        if p.returncode == self._NOT_FOUND_RC:
            return ""
        if p.returncode != 0:
            raise KeychainError("security find-generic-password rc=%d: %s"
                                % (p.returncode, (p.stderr or "").strip()))
        # -w gibt nur das Passwort plus Newline aus.
        return (p.stdout or "").rstrip("\r\n")

    def set(self, value: str) -> None:
        # -U überschreibt ein bestehendes Item statt mit „already exists“ zu scheitern.
        p = self._security("add-generic-password -a %s -s %s -U -w %s"
                           % (self._quote(self.account), SERVICE, self._quote(value)))
        if p.returncode != 0:
            raise KeychainError("security add-generic-password rc=%d: %s"
                                % (p.returncode, (p.stderr or "").strip()))

    def delete(self) -> None:
        p = self._security("delete-generic-password -a %s -s %s"
                           % (self._quote(self.account), SERVICE))
        if p.returncode not in (0, self._NOT_FOUND_RC):
            raise KeychainError("security delete-generic-password rc=%d: %s"
                                % (p.returncode, (p.stderr or "").strip()))


# --------------------------------------------------------------------------- Linux: Datei

class _FileBackend:
    """Linux-Fallback ohne secret-tool: Datei mit 0600 in einem 0700-Verzeichnis.
    Gleiches Muster wie write_secret() in scripts/setup.py."""

    name = "Datei (0600) — secret-tool fehlt"

    def __init__(self, account: str, base_dir: Optional[str] = None):
        self.account = account
        self.base_dir = base_dir or os.path.join(
            os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
            "ai-rem", "keyring")

    @property
    def path(self) -> str:
        return os.path.join(self.base_dir, self.account)

    def get(self) -> str:
        try:
            with open(self.path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def set(self, value: str) -> None:
        os.makedirs(self.base_dir, mode=0o700, exist_ok=True)
        os.chmod(self.base_dir, 0o700)  # bestehendes Verzeichnis nachziehen
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value.strip() + "\n")
        os.chmod(self.path, 0o600)  # bestehende Datei mit lockereren Rechten nachziehen

    def delete(self) -> None:
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


# --------------------------------------------------------------------------- Linux: libsecret

class _LibsecretBackend:
    """`secret-tool` (libsecret/GNOME Keyring, KWallet via Portal). Secret geht bei
    store über stdin. Schlägt store fehl (kein DBus, keine Session, kein
    entsperrter Keyring), fällt das Backend auf _FileBackend zurück und meldet
    das über backend_name(), damit es im Status sichtbar bleibt."""

    name = "libsecret"

    def __init__(self, account: str, run: Callable = subprocess.run,
                 fallback: Optional[_FileBackend] = None):
        self.account = account
        self.run = run
        self.fallback = fallback or _FileBackend(account)
        self.degraded = False  # True, sobald store einmal scheiterte

    def _args(self, verb: str, *extra: str):
        return ["secret-tool", verb, *extra, "service", SERVICE, "account", self.account]

    def get(self) -> str:
        if self.degraded:
            return self.fallback.get()
        p = self.run(self._args("lookup"), capture_output=True, text=True,
                     timeout=TIMEOUT_S)
        if p.returncode != 0:
            # Fehlt das Item, liefert lookup rc 1; ein früherer Datei-Fallback
            # (neuer Prozess kennt degraded nicht) muss trotzdem gefunden werden.
            return self.fallback.get()
        return (p.stdout or "").rstrip("\r\n")

    def set(self, value: str) -> None:
        if not self.degraded:
            p = self.run(self._args("store", "--label=ai-rem device token"),
                         input=value, capture_output=True, text=True,
                         timeout=TIMEOUT_S)
            if p.returncode == 0:
                self.fallback.delete()  # veralteten Datei-Fallback nicht liegen lassen
                return
            self.degraded = True
        self.fallback.set(value)

    def delete(self) -> None:
        if not self.degraded:
            self.run(self._args("clear"), capture_output=True, text=True,
                     timeout=TIMEOUT_S)
        self.fallback.delete()

    @property
    def backend_name(self) -> str:
        return ("Datei (0600) — secret-tool ohne Session, Fallback"
                if self.degraded else self.name)


# --------------------------------------------------------------------------- Windows

# Konstanten aus wincred.h.
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", ctypes.c_uint32),
        ("Type", ctypes.c_uint32),
        ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", ctypes.c_uint32),
        ("CredentialBlob", ctypes.c_void_p),
        ("Persist", ctypes.c_uint32),
        ("AttributeCount", ctypes.c_uint32),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


def _cred_struct(target: str, blob: bytes):
    """CREDENTIALW für CredWriteW aufbauen. Gibt (struct, buffer) zurück — der
    Buffer muss bis zum Aufruf am Leben bleiben, sonst zeigt CredentialBlob ins Leere.
    Plattformunabhängig (ctypes.Structure), damit auf macOS/Linux testbar."""
    buf = ctypes.create_string_buffer(blob, len(blob))
    cred = _CREDENTIALW()
    cred.Flags = 0
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.Comment = "ai-rem device token"
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.c_void_p)
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.AttributeCount = 0
    cred.Attributes = None
    cred.TargetAlias = None
    cred.UserName = SERVICE
    return cred, buf


def _last_error(adv) -> int:
    # ctypes.get_last_error() existiert nur unter Windows; ein Fake-adv darf
    # stattdessen last_error() mitbringen, damit die Fehlerpfade testbar sind.
    fn = getattr(adv, "last_error", None) or getattr(ctypes, "get_last_error", None)
    return int(fn()) if fn else 0


def _cred_write(adv, target: str, blob: bytes) -> None:
    cred, buf = _cred_struct(target, blob)
    if not adv.CredWriteW(ctypes.byref(cred), 0):
        raise KeychainError("CredWriteW fehlgeschlagen (Win32-Fehler %d)" % _last_error(adv))
    del buf  # explizit: Lebensdauer bis hierher gesichert


def _cred_read(adv, target: str) -> bytes:
    """Blob lesen; b"" wenn kein Credential existiert (ERROR_NOT_FOUND)."""
    pcred = ctypes.POINTER(_CREDENTIALW)()
    if not adv.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        err = _last_error(adv)
        if err == ERROR_NOT_FOUND:
            return b""
        raise KeychainError("CredReadW fehlgeschlagen (Win32-Fehler %d)" % err)
    try:
        c = pcred.contents
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
    finally:
        adv.CredFree(pcred)


def _cred_delete(adv, target: str) -> None:
    if not adv.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        err = _last_error(adv)
        if err != ERROR_NOT_FOUND:
            raise KeychainError("CredDeleteW fehlgeschlagen (Win32-Fehler %d)" % err)


def _load_advapi32():
    # Lazy: ctypes.windll gibt es nur unter Windows; use_last_error für GetLastError.
    return ctypes.WinDLL("advapi32", use_last_error=True)


class _WindowsBackend:
    """Windows Credential Manager via advapi32. Blob ist UTF-16LE, wie es
    `cmdkey`/PowerShell-Tools erwarten; TargetName `ai-rem/<account>`."""

    name = "Windows Credential Manager"

    def __init__(self, account: str, adv=None):
        self.account = account
        self._adv = adv

    @property
    def adv(self):
        if self._adv is None:
            self._adv = _load_advapi32()
        return self._adv

    @property
    def target(self) -> str:
        return "%s/%s" % (SERVICE, self.account)

    def get(self) -> str:
        return _cred_read(self.adv, self.target).decode("utf-16-le")

    def set(self, value: str) -> None:
        _cred_write(self.adv, self.target, value.encode("utf-16-le"))

    def delete(self) -> None:
        _cred_delete(self.adv, self.target)


# --------------------------------------------------------------------------- Auswahl

_BACKEND = None  # Modul-Cache; _reset_backend() für Tests


def _select_backend(platform: Optional[str] = None, which: Optional[Callable] = None):
    # sys.platform/shutil.which erst hier auflösen, damit Tests sie monkeypatchen können.
    platform = platform or sys.platform
    which = which or shutil.which
    if platform == "darwin":
        return _MacBackend(ACCOUNT)
    if platform == "win32":
        return _WindowsBackend(ACCOUNT)
    if which("secret-tool"):
        return _LibsecretBackend(ACCOUNT)
    return _FileBackend(ACCOUNT)


def _backend():
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = _select_backend()
    return _BACKEND


def _reset_backend() -> None:
    global _BACKEND
    _BACKEND = None


def get() -> str:
    """Geräte-Token; "" wenn keiner abgelegt ist (nie Exception bei „nicht gefunden“)."""
    return _backend().get()


def set(value: str) -> None:  # noqa: A001 — bewusst wie keyring.set_password benannt
    """Token ablegen, bestehenden überschreiben."""
    _backend().set(value)


def delete() -> None:
    """Token entfernen; idempotent."""
    _backend().delete()


def backend_name() -> str:
    b = _backend()
    return getattr(b, "backend_name", None) or b.name

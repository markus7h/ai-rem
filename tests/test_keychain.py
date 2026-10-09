"""Tests für lib/keychain: alle Backends ohne echte Keychains (Fakes für
subprocess.run / shutil.which / advapi32). Läuft auch ohne pytest über den
__main__-Block (pytest ist auf der Workstation nicht installiert)."""
import ctypes
import os
import stat
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import keychain as kc  # noqa: E402

ACCT = "unit-test"
SECRET = "tok-abc123"  # pragma: allowlist secret


class FakeRun:
    """Ersatz für subprocess.run: zeichnet Aufrufe auf, antwortet per Verb-Tabelle."""

    def __init__(self, responses):
        self.responses = responses  # verb → (rc, stdout, stderr)
        self.calls = []

    def __call__(self, argv, input=None, capture_output=False, text=False, **kw):
        self.calls.append(SimpleNamespace(argv=list(argv), input=input))
        text_ = input if input is not None else ""
        verb = next((v for v in self.responses if v in text_ or v in argv), None)
        rc, out, err = self.responses.get(verb, (1, "", "unbekanntes Kommando"))
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


# --------------------------------------------------------------------------- macOS

def test_mac_get_reads_stdout_and_keeps_secret_off_argv():
    run = FakeRun({"find-generic-password": (0, SECRET + "\n", "")})
    assert kc._MacBackend(ACCT, run=run).get() == SECRET
    call = run.calls[0]
    assert call.argv == ["security", "-i"]
    assert "find-generic-password -a \"%s\" -s ai-rem -w" % ACCT in call.input


def test_mac_get_not_found_rc44_is_empty():
    run = FakeRun({"find-generic-password": (44, "", "could not be found")})
    assert kc._MacBackend(ACCT, run=run).get() == ""


def test_mac_get_other_rc_raises_with_stderr():
    run = FakeRun({"find-generic-password": (36, "", "User interaction is not allowed.")})
    try:
        kc._MacBackend(ACCT, run=run).get()
    except kc.KeychainError as e:
        assert "User interaction" in str(e)
    else:
        raise AssertionError("KeychainError erwartet")


def test_mac_set_uses_update_flag_and_stdin_only():
    run = FakeRun({"add-generic-password": (0, "", "")})
    kc._MacBackend(ACCT, run=run).set(SECRET)
    call = run.calls[0]
    assert call.argv == ["security", "-i"]  # Secret darf nie in argv stehen
    assert SECRET not in " ".join(call.argv)
    assert " -U " in call.input
    assert call.input.rstrip("\n").endswith('-w "%s"' % SECRET)


def test_mac_set_escapes_quotes_and_backslashes():
    run = FakeRun({"add-generic-password": (0, "", "")})
    kc._MacBackend(ACCT, run=run).set('a"b\\c')  # pragma: allowlist secret
    assert run.calls[0].input.rstrip("\n").endswith('-w "a\\"b\\\\c"')


def test_mac_delete_is_idempotent():
    run = FakeRun({"delete-generic-password": (44, "", "could not be found")})
    kc._MacBackend(ACCT, run=run).delete()  # kein Fehler
    assert "delete-generic-password" in run.calls[0].input


# --------------------------------------------------------------------------- Linux

def test_linux_without_secret_tool_writes_0600_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    b = kc._select_backend(platform="linux", which=lambda name: None)
    assert isinstance(b, kc._FileBackend)
    assert b.name == "Datei (0600) — secret-tool fehlt"
    b.set(SECRET)
    path = os.path.join(str(tmp_path), "ai-rem", "keyring", kc.ACCOUNT)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
    assert b.get() == SECRET
    b.delete()
    b.delete()  # idempotent
    assert b.get() == ""


def test_linux_with_secret_tool_selects_libsecret():
    b = kc._select_backend(platform="linux", which=lambda name: "/usr/bin/secret-tool")
    assert isinstance(b, kc._LibsecretBackend)
    assert b.name == "libsecret"


def test_libsecret_lookup_and_store(tmp_path):
    run = FakeRun({"lookup": (0, SECRET + "\n", ""), "store": (0, "", ""), "clear": (0, "", "")})
    fb = kc._FileBackend(ACCT, base_dir=str(tmp_path))
    b = kc._LibsecretBackend(ACCT, run=run, fallback=fb)
    assert b.get() == SECRET
    assert run.calls[0].argv == ["secret-tool", "lookup", "service", "ai-rem", "account", ACCT]
    b.set(SECRET)
    store = run.calls[1]
    assert store.argv[:3] == ["secret-tool", "store", "--label=ai-rem device token"]
    assert store.input == SECRET and SECRET not in " ".join(store.argv)
    b.delete()
    assert run.calls[2].argv[1] == "clear"


def test_libsecret_lookup_failure_is_empty(tmp_path):
    run = FakeRun({"lookup": (1, "", "")})
    b = kc._LibsecretBackend(ACCT, run=run, fallback=kc._FileBackend(ACCT, base_dir=str(tmp_path)))
    assert b.get() == ""


def test_libsecret_store_failure_falls_back_to_file(tmp_path):
    run = FakeRun({"store": (1, "", "secret-tool: Cannot autolaunch D-Bus without X11"),
                   "lookup": (1, "", "")})
    fb = kc._FileBackend(ACCT, base_dir=str(tmp_path))
    b = kc._LibsecretBackend(ACCT, run=run, fallback=fb)
    b.set(SECRET)
    assert os.path.isfile(fb.path)
    assert stat.S_IMODE(os.stat(fb.path).st_mode) == 0o600
    assert b.get() == SECRET
    assert b.degraded and "Fallback" in b.backend_name  # sichtbar im Status


def test_libsecret_locked_keyring_names_reason(tmp_path):
    # SSH ohne Desktop-Login: secret-tool ist da, der Login-Keyring bleibt gesperrt.
    run = FakeRun({"store": (1, "", "secret-tool: Cannot create an item in a locked collection"),
                   "lookup": (1, "", "")})
    b = kc._LibsecretBackend(ACCT, run=run, fallback=kc._FileBackend(ACCT, base_dir=str(tmp_path)))
    b.set(SECRET)
    assert b.backend_name == "Datei (0600) — Schlüsselbund gesperrt (SSH/headless), Fallback"
    assert "secret-tool fehlt" not in b.backend_name


def test_libsecret_fresh_process_reports_file_when_token_lives_there(tmp_path):
    # Neuer Prozess (doctor): degraded ist unbekannt, der Token kommt aber aus der
    # Datei — angezeigt wird die Datei, nicht „libsecret“.
    fb = kc._FileBackend(ACCT, base_dir=str(tmp_path))
    fb.set(SECRET)
    b = kc._LibsecretBackend(ACCT, run=FakeRun({"lookup": (1, "", "")}), fallback=fb)
    assert b.get() == SECRET
    assert b.backend_name.startswith("Datei (0600)")


def test_libsecret_lookup_hit_reports_libsecret(tmp_path):
    fb = kc._FileBackend(ACCT, base_dir=str(tmp_path))
    fb.set("alt")  # liegengebliebene Datei ändert nichts, solange lookup trifft
    b = kc._LibsecretBackend(ACCT, run=FakeRun({"lookup": (0, SECRET + "\n", "")}), fallback=fb)
    assert b.get() == SECRET
    assert b.backend_name == "libsecret"


# --------------------------------------------------------------------------- Windows

class FakeAdvapi:
    """advapi32-Ersatz mit dict-Store; hält Structs/Buffer am Leben wie die DLL."""

    def __init__(self):
        self.store = {}
        self._err = 0
        self._alive = []

    def last_error(self):
        return self._err

    def CredWriteW(self, cred_ref, flags):
        c = cred_ref._obj
        assert c.Type == kc.CRED_TYPE_GENERIC and c.Persist == kc.CRED_PERSIST_LOCAL_MACHINE
        self.store[c.TargetName] = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
        return 1

    def CredReadW(self, target, typ, flags, pcred_ref):
        if target not in self.store:
            self._err = kc.ERROR_NOT_FOUND
            return 0
        cred, buf = kc._cred_struct(target, self.store[target])
        self._alive.append((cred, buf))
        pcred_ref._obj.contents = cred
        return 1

    def CredDeleteW(self, target, typ, flags):
        if target not in self.store:
            self._err = kc.ERROR_NOT_FOUND
            return 0
        del self.store[target]
        return 1

    def CredFree(self, pcred):
        return None


def test_win_cred_struct_is_platform_independent():
    blob = SECRET.encode("utf-16-le")
    cred, buf = kc._cred_struct("ai-rem/" + ACCT, blob)
    assert cred.TargetName == "ai-rem/" + ACCT
    assert cred.CredentialBlobSize == 2 * len(SECRET)
    assert ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize) == blob
    assert cred.Type == 1 and cred.Persist == 2


def test_win_backend_roundtrip_with_fake_dll():
    adv = FakeAdvapi()
    b = kc._WindowsBackend(ACCT, adv=adv)
    assert b.target == "ai-rem/" + ACCT
    assert b.get() == ""  # not found → ""
    b.set(SECRET)
    assert adv.store[b.target] == SECRET.encode("utf-16-le")
    assert b.get() == SECRET
    b.set("neu")
    assert b.get() == "neu"
    b.delete()
    b.delete()  # idempotent
    assert b.get() == ""


# --------------------------------------------------------------------------- Auswahl

def test_backend_selection_by_platform(monkeypatch):
    for platform, which_result, cls in [
        ("darwin", None, kc._MacBackend),
        ("win32", None, kc._WindowsBackend),
        ("linux", "/usr/bin/secret-tool", kc._LibsecretBackend),
        ("linux", None, kc._FileBackend),
    ]:
        monkeypatch.setattr(sys, "platform", platform)
        monkeypatch.setattr(kc.shutil, "which", lambda name, _r=which_result: _r)
        kc._reset_backend()
        assert type(kc._backend()) is cls, platform
    kc._reset_backend()


def test_module_api_delegates_to_backend(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        name="Fake", get=lambda: "v", set=lambda v: calls.append(("set", v)),
        delete=lambda: calls.append(("delete",)))
    monkeypatch.setattr(kc, "_BACKEND", fake)
    assert kc.get() == "v"
    kc.set("x")
    kc.delete()
    assert kc.backend_name() == "Fake"
    assert calls == [("set", "x"), ("delete",)]
    kc._reset_backend()


# --------------------------------------------------------------------------- ohne pytest

if __name__ == "__main__":
    # Minimale Shims für monkeypatch/tmp_path, damit der Lauf ohne pytest geht.
    import inspect
    import shutil
    import tempfile
    import traceback

    class _Monkeypatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def setenv(self, name, value):
            self._undo.append((os.environ, name, os.environ.get(name)))
            os.environ[name] = value

        def undo(self):
            for obj, name, old in reversed(self._undo):
                if obj is os.environ:
                    if old is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = old
                else:
                    setattr(obj, name, old)
            self._undo = []

    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        mp, tmp = _Monkeypatch(), tempfile.mkdtemp(prefix="kc-test-")
        kwargs = {}
        params = inspect.signature(fn).parameters
        if "monkeypatch" in params:
            kwargs["monkeypatch"] = mp
        if "tmp_path" in params:
            kwargs["tmp_path"] = tmp
        try:
            fn(**kwargs)
            print("PASS", name)
        except Exception:
            failed += 1
            print("FAIL", name)
            traceback.print_exc()
        finally:
            mp.undo()
            shutil.rmtree(tmp, ignore_errors=True)
            kc._reset_backend()
    print("%d/%d bestanden" % (len(tests) - failed, len(tests)))
    sys.exit(1 if failed else 0)

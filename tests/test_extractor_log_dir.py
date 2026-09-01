"""Der Extraktor muss last-run.json in dasselbe auto-memory schreiben, aus dem der
SessionStart-Check liest.

Regression: LOG_DIR hing hart an ~/.claude, waehrend hooks/auto-memory.py und
hooks/system-check.py $CLAUDE_CONFIG_DIR folgen. In einer Session mit eigenem
Config-Dir (CLAUDE_CONFIG_DIR=~/.claude-<profil>) landeten die Erfolge im einen
und die Fehler im anderen Verzeichnis — der Check sah dort nie ein last-run.json
und meldete bei jedem Sessionstart "Auto-Memory gestört", obwohl der Ingest lief.
"""
import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib import extractor  # noqa: E402


def _log_dir_with(monkeypatch, config_dir):
    if config_dir is None:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", config_dir)
    return importlib.reload(extractor).LOG_DIR


def test_log_dir_folgt_config_dir(monkeypatch, tmp_path):
    assert _log_dir_with(monkeypatch, str(tmp_path)) == tmp_path / "auto-memory"


def test_log_dir_nimmt_ersten_pfad_der_liste(monkeypatch, tmp_path):
    # Claude Code erlaubt mehrere Config-Dirs; gelesen wird aus dem ersten.
    joined = os.pathsep.join([str(tmp_path), "/anderes/dir"])
    assert _log_dir_with(monkeypatch, joined) == tmp_path / "auto-memory"


def test_log_dir_faellt_auf_home_zurueck(monkeypatch):
    expected = os.path.join(os.path.expanduser("~/.claude"), "auto-memory")
    assert str(_log_dir_with(monkeypatch, None)) == expected


def test_abgeleitete_pfade_ziehen_mit(monkeypatch, tmp_path):
    _log_dir_with(monkeypatch, str(tmp_path))
    for p in (extractor.LAST_RUN, extractor.FALLBACK_MD, extractor.PENDING_JSONL):
        assert p.parent == tmp_path / "auto-memory"


def teardown_module():
    # Modulzustand fuer nachfolgende Tests wieder auf die echte Umgebung bringen.
    os.environ.pop("CLAUDE_CONFIG_DIR", None)
    importlib.reload(extractor)

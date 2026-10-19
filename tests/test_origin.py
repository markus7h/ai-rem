"""Session-Herkunft (lib/origin.py): Projekt und Kontext aus cwd + git statt LLM-Raten.

Echte Temp-Repos (git init + remote add), kein Server: die Projects kommen als Liste
herein wie aus memory_project_index. Ad-hoc-Ausnahmen (Home, Temp) werden pro Test
explizit gesetzt, weil tmp_path selbst unter einer Temp-Wurzel liegt.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib import origin  # noqa: E402
from lib.origin import parse_remote, resolve_origin  # noqa: E402

RULES = [
    {"owner": "markusth-bi", "emails": ["markus@firma.example"], "context": "work"},
    {"owner": "markus7h", "emails": ["markus@privat.example"], "context": "private"},
]
NO_ADHOC = {"skip_dirs": [], "tmp_roots": []}


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _repo(path, remote=None, email=None):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    if remote:
        _git(path, "remote", "add", "origin", remote)
    if email:
        _git(path, "config", "user.email", email)
    return path


def test_parse_remote_formen():
    assert parse_remote("https://github.com/markus7h/ai-rem.git") == ("markus7h", "ai-rem")
    assert parse_remote("git@github-personal:markus7h/projekt-x.git") == ("markus7h", "projekt-x")
    assert parse_remote("ssh://git@host:22/Org/x.git") == ("Org", "x")
    assert parse_remote("/srv/git/foo.git") == ("", "foo")
    assert parse_remote("") == ("", "")


def test_https_remote_owner_regel(tmp_path):
    r = _repo(tmp_path / "ai-rem", "https://github.com/Markus7h/ai-rem.git")
    o = resolve_origin(str(r / "."), RULES, [], **NO_ADHOC)
    assert o["repo"] == "Markus7h/ai-rem"
    assert o["project"] == "ai-rem" and not o["project_exists"]
    assert o["context"] == "private" and o["source"] == "owner"
    assert not o["ask"]


def test_ssh_alias_remote(tmp_path):
    r = _repo(tmp_path / "dg", "git@github-work:markusth-bi/projekt-y.git")
    (r / "sub").mkdir()
    o = resolve_origin(str(r / "sub"), RULES, [], **NO_ADHOC)
    assert o["project"] == "projekt-y"  # Repo-Name, nicht Ordnername
    assert o["context"] == "work"


def test_org_repo_ueber_git_mail(tmp_path):
    r = _repo(tmp_path / "kaiguard", "https://github.com/kaiguard/kaiguard.git",
              email="Markus@Firma.example")
    o = resolve_origin(str(r), RULES, [], **NO_ADHOC)
    assert o["owner"] == "kaiguard"
    assert o["context"] == "work" and o["source"] == "email"


def test_org_repo_ohne_regel_fragt_nach_kontext(tmp_path):
    r = _repo(tmp_path / "kaiguard", "https://github.com/kaiguard/kaiguard.git")
    _git(r, "config", "user.email", "fremd@example.org")
    o = resolve_origin(str(r), RULES, [], **NO_ADHOC)
    assert o["project"] == "kaiguard" and o["context"] == ""
    assert o["ask"] and o["suggestions"] == ["kaiguard"]


def test_worktree_gehoert_zum_hauptrepo(tmp_path):
    main = _repo(tmp_path / "kaiguard", "https://github.com/kaiguard/kaiguard.git",
                 email="markus@firma.example")
    _git(main, "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    wt = tmp_path / "kaiguard-244"
    _git(main, "worktree", "add", "-q", str(wt))
    o = resolve_origin(str(wt), RULES, [], **NO_ADHOC)
    assert o["project"] == "kaiguard" and o["context"] == "work"


def test_worktree_ohne_remote_nimmt_hauptordner(tmp_path):
    main = _repo(tmp_path / "werkzeug")
    _git(main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i")
    wt = tmp_path / "werkzeug-fix"
    _git(main, "worktree", "add", "-q", str(wt))
    assert resolve_origin(str(wt), RULES, [], **NO_ADHOC)["project"] == "werkzeug"


def test_bestehendes_projekt_ueber_repos_und_name(tmp_path):
    r = _repo(tmp_path / "kaiguard", "https://github.com/kaiguard/kaiguard.git")
    projects = [{"name": "KaiGuard Plattform", "context": "work",
                 "repos": ["KaiGuard/kaiguard"], "dirs": [], "keywords": []}]
    o = resolve_origin(str(r), RULES, projects, **NO_ADHOC)
    assert o["project"] == "KaiGuard Plattform" and o["project_exists"]
    assert o["project_source"] == "repos"
    # Kein Owner-/Mail-Treffer: Kontext des zugeordneten Projects.
    assert o["context"] == "work" and o["source"] == "project" and not o["ask"]

    o = resolve_origin(str(r), RULES, [{"name": "KAIGUARD", "context": ""}], **NO_ADHOC)
    assert o["project"] == "KAIGUARD" and o["project_source"] == "name"


def test_verzeichnis_zuordnung_ohne_repo(tmp_path):
    kunde = tmp_path / "Luschendorf"
    (kunde / "Angebote").mkdir(parents=True)
    projects = [{"name": "Luschendorf", "context": "work", "dirs": [str(kunde)]},
                {"name": "Andere", "context": "private", "dirs": [str(tmp_path)]}]
    o = resolve_origin(str(kunde / "Angebote"), RULES, projects, **NO_ADHOC)
    assert o["project"] == "Luschendorf"  # laengster Praefix gewinnt
    assert o["context"] == "work" and o["source"] == "dirs" and not o["ask"]


def test_ohne_repo_fragt_mit_vorschlaegen(tmp_path):
    d = tmp_path / "luschendorf-2026"
    d.mkdir()
    projects = [{"name": "Luschendorf", "keywords": []},
                {"name": "Garten", "keywords": ["luschendorf"]},
                {"name": "ai-rem", "keywords": []}]
    o = resolve_origin(str(d), RULES, projects, **NO_ADHOC)
    assert o["ask"] and o["project"] == "" and o["context"] == ""
    assert o["suggestions"][-1] == "neu: luschendorf-2026"
    assert set(o["suggestions"][:-1]) == {"Luschendorf", "Garten"}


def test_home_und_temp_werden_nicht_gefragt(tmp_path):
    home = tmp_path / "home"
    (home / "Kunde").mkdir(parents=True)
    o = resolve_origin(str(home), RULES, [], skip_dirs=[str(home)], tmp_roots=[])
    assert not o["ask"] and not o["project"]
    # Unterordner von Home sind keine Ad-hoc-Orte.
    assert resolve_origin(str(home / "Kunde"), RULES, [], skip_dirs=[str(home)], tmp_roots=[])["ask"]
    # Default-Ausnahmen: tmp_path liegt selbst unter einer Temp-Wurzel.
    assert not resolve_origin(str(home / "Kunde"), RULES, [])["ask"]


def test_defaults_ohne_serverregeln(tmp_path):
    r = _repo(tmp_path / "x", "https://github.com/markusth-bi/x.git")
    assert resolve_origin(str(r), None, [], **NO_ADHOC)["context"] == "work"


class _Client:
    def __init__(self, projects):
        self.projects = projects
        self.calls = []

    def client_config(self):
        return {"context_rules": RULES}

    def call(self, tool, args):
        self.calls.append((tool, args))
        if tool == "memory_project_index":
            return json.dumps(self.projects)
        return f"ok {args.get('name')}"


def test_ensure_project_legt_an_und_traegt_repo_nach():
    c = _Client([])
    o = {"project": "ai-rem", "repo": "markus7h/ai-rem", "context": "private"}
    origin.ensure_project(c, o)
    tool, args = c.calls[-1]
    assert tool == "memory_add" and args["type"] == "Project" and args["context"] == "private"
    assert args["extra"]["repos"] == ["markus7h/ai-rem"]

    c = _Client([{"name": "AI-REM", "repos": ["x/y"]}])
    origin.ensure_project(c, o)
    assert c.calls[-1][1] == {"name": "AI-REM", "type": "Project",
                              "extra": {"repos": ["x/y", "markus7h/ai-rem"]}}

    c = _Client([{"name": "ai-rem", "repos": ["Markus7h/AI-rem"]}])
    assert origin.ensure_project(c, o) == ""
    assert origin.ensure_project(_Client([]), {"project": "x", "repo": ""}) == ""


def test_map_dir_haengt_an_und_legt_an(tmp_path):
    c = _Client([{"name": "Luschendorf", "dirs": ["/alt"]}])
    origin.map_dir(c, str(tmp_path), "luschendorf", "work")
    assert c.calls[-1][1] == {"name": "Luschendorf", "type": "Project",
                              "extra": {"dirs": ["/alt", str(tmp_path)]}, "context": "work"}
    c = _Client([])
    origin.map_dir(c, str(tmp_path), "Neu")
    args = c.calls[-1][1]
    assert args["name"] == "Neu" and args["extra"]["status"] == "aktiv" and "context" not in args


def test_cli_origin_json(tmp_path, monkeypatch, capsys):
    """`ai-rem origin --json` liefert genau das resolve-Ergebnis (fuer den SessionStart-Hook)."""
    import importlib.machinery
    import importlib.util

    loader = importlib.machinery.SourceFileLoader("ai_rem_cli", os.path.join(ROOT, "bin", "ai-rem"))
    spec = importlib.util.spec_from_loader("ai_rem_cli", loader)
    cli = importlib.util.module_from_spec(spec)
    loader.exec_module(cli)
    r = _repo(tmp_path / "proj", "https://github.com/markus7h/proj.git")
    args = cli.build_parser().parse_args(["origin", "--cwd", str(r), "--json"])
    args.func(_Client([]), args)
    out = json.loads(capsys.readouterr().out)
    assert out["project"] == "proj" and out["context"] == "private"


@pytest.mark.parametrize("fmt", ["claude", "opencode"])
def test_transcript_cwd(tmp_path, fmt):
    from lib.extractor import transcript_cwd

    p = tmp_path / "t.json"
    if fmt == "claude":
        p.write_text('{"type":"summary"}\n{"type":"user","cwd":"/w/ai-rem","message":{}}\n')
    else:
        p.write_text(json.dumps({"format": "opencode", "cwd": "/w/oc", "messages": []}))
    assert transcript_cwd(p) == ("/w/ai-rem" if fmt == "claude" else "/w/oc")

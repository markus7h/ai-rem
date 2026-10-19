"""Session-Herkunft: aus dem Arbeitsverzeichnis Projekt und Kontext ableiten.

Bisher riet das Extraktions-LLM beides aus dem Transcript-Text — mit dem Ergebnis,
dass private Repos als [work] landeten und aus Release-Notizen Pseudo-Projekte
wurden. cwd, git-Remote und git-Mail sind dagegen eindeutig:

- Kontext: Remote-Owner (markus7h → private, markusth-bi → work) → git user.email
  im Repo (Org-Repos wie kaiguard/kaiguard) → gespeicherte Verzeichnis-Zuordnung
  (Project extra.dirs) → leer, dann fragt der SessionStart-Hook einmal nach.
- Projekt: Project mit extra.repos ∋ owner/name → Project mit extra.dirs-Präfix →
  Project gleichen Namens wie das Repo → Repo-Name (wird bei Bedarf angelegt).
  Ohne Repo und ohne Zuordnung: ask + Vorschläge.

Reine stdlib (wird mit der CLI ausgeliefert, Python 3.8+). Die Regeln kommen vom
Server (/api/client-config → context_rules), die Projects über memory_project_index.
"""
from __future__ import annotations

import difflib
import json
import os
import subprocess
import tempfile
from typing import Optional
from urllib.parse import urlparse

# Fallback, wenn der Server (< 1.10) noch keine context_rules liefert.
DEFAULT_RULES = [
    {"owner": "markusth-bi", "emails": [], "context": "work"},
    {"owner": "markus7h", "emails": [], "context": "private"},
    {"owner": "kaiguard", "emails": [], "context": "private"},
]
GIT_TIMEOUT_S = 3


def _git(cwd: str, *args: str) -> str:
    try:
        p = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                           timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def parse_remote(url: str) -> tuple:
    """(owner, name) aus https-, ssh://- und scp-artigen Remotes
    (git@github-personal:owner/projekt.git). Bei Subgruppen zählt das letzte
    Pfadpaar; ein lokaler Pfad liefert nur den Namen."""
    url = (url or "").strip()
    if not url:
        return "", ""
    if "://" in url:
        path = urlparse(url).path
    elif ":" in url and not os.path.isabs(url):
        path = url.split(":", 1)[1]
    else:
        path = url
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if not parts:
        return "", ""
    name = parts[-1]
    if name.endswith(".git"):
        name = name[:-4]
    remote = "://" in url or (":" in url and not os.path.isabs(url))
    owner = parts[-2] if remote and len(parts) >= 2 else ""
    return owner, name


def git_info(cwd: str) -> dict:
    """{root, main_root, url, owner, name, email} — leer, wenn cwd in keinem Repo liegt.

    main_root über --git-common-dir: ein Worktree (~/Code/kaiguard-244) gehört zum
    Haupt-Repo, nicht zu einem eigenen Projekt "kaiguard-244"."""
    root = _git(cwd, "rev-parse", "--show-toplevel")
    if not root:
        return {}
    common = _git(cwd, "rev-parse", "--git-common-dir")
    if common and not os.path.isabs(common):
        common = os.path.normpath(os.path.join(cwd, common))
    if common and os.path.basename(common) == ".git":
        main_root = os.path.dirname(common)
    else:
        main_root = common or root
    url = _git(cwd, "config", "--get", "remote.origin.url")
    if not url:
        first = (_git(cwd, "remote").splitlines() or [""])[0]
        url = _git(cwd, "config", "--get", f"remote.{first}.url") if first else ""
    owner, name = parse_remote(url)
    if not name:
        name = os.path.basename(main_root.rstrip("/\\"))
        if name.endswith(".git"):
            name = name[:-4]
    return {"root": root, "main_root": main_root, "url": url, "owner": owner, "name": name,
            "email": _git(cwd, "config", "user.email")}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def _variants(path: str) -> set:
    # SMB-Mount vs. Symlink (~/myCode → /Volumes/markus/myCode): beide Schreibweisen zählen.
    return {_norm(path), os.path.normcase(os.path.realpath(os.path.expanduser(path)))}


def _under(path: str, base: str) -> bool:
    for p in _variants(path):
        for b in _variants(base):
            try:
                if os.path.commonpath([p, b]) == b:
                    return True
            except ValueError:  # verschiedene Laufwerke (Windows)
                continue
    return False


def _adhoc_dirs() -> tuple:
    """(exakt, Wurzeln): Home und / nur exakt — darunter liegen echte Projekte;
    Temp-/Scratchpad-Wurzeln mit allem darunter."""
    return ([os.path.expanduser("~"), "/"],
            ["/tmp", "/private/tmp", "/var/folders", "/private/var/folders", tempfile.gettempdir()])


def _is_adhoc(cwd: str, exact: list, roots: list) -> bool:
    """Dort startet man Ad-hoc-Sessions; eine Rückfrage bei jedem Start wäre Lärm."""
    n = _variants(cwd)
    return (any(n & _variants(d) for d in exact if d)
            or any(_under(cwd, r) for r in roots if r))


def _suggest(folder: str, projects: list, limit: int = 3) -> list:
    f = folder.lower()
    scored = []
    for p in projects:
        best = 0.0
        for cand in [p.get("name", ""), *(p.get("keywords") or [])]:
            c = str(cand).lower()
            if not c:
                continue
            r = difflib.SequenceMatcher(None, f, c).ratio()
            if len(c) >= 3 and (c in f or f in c):
                r = max(r, 0.8)
            best = max(best, r)
        if best >= 0.5:
            scored.append((best, p["name"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [n for _, n in scored[:limit]]


def resolve_origin(cwd: str, rules: Optional[list] = None, projects: Optional[list] = None,
                   skip_dirs: Optional[list] = None, tmp_roots: Optional[list] = None) -> dict:
    """Herkunft einer Session. projects: [{name, context, repos, dirs, keywords}].
    skip_dirs/tmp_roots überschreiben die Ad-hoc-Ausnahmen (Tests)."""
    rules = rules if rules else DEFAULT_RULES
    projects = projects or []
    exact, roots = _adhoc_dirs()
    exact = exact if skip_dirs is None else skip_dirs
    roots = roots if tmp_roots is None else tmp_roots
    out = {"cwd": cwd, "repo": "", "owner": "", "email": "", "context": "", "project": "",
           "project_exists": False, "source": "", "project_source": "",
           "ask": False, "suggestions": []}
    if not cwd or not os.path.isdir(cwd):
        return out

    g = git_info(cwd)
    owner, name = g.get("owner", ""), g.get("name", "")
    full = f"{owner}/{name}" if owner else name
    out.update(repo=full, owner=owner, email=g.get("email", ""))

    def _by(pred):
        return next((p for p in projects if pred(p)), None)

    # Längster dirs-Präfix gewinnt (Unterordner-Zuordnung schlägt den Elternordner).
    dir_hit, dir_len = None, -1
    for p in projects:
        for d in p.get("dirs") or []:
            if _under(cwd, d) and len(_norm(d)) > dir_len:
                dir_hit, dir_len = p, len(_norm(d))

    proj = None
    if g and owner:
        proj = _by(lambda p: full.lower() in {str(r).lower() for r in p.get("repos") or []})
        out["project_source"] = "repos" if proj else ""
    if proj is None and dir_hit is not None:
        proj, out["project_source"] = dir_hit, "dirs"
    if proj is None and g:
        proj = _by(lambda p: p.get("name", "").lower() == name.lower())
        out["project_source"] = "name" if proj else ""
    if proj is not None:
        out.update(project=proj["name"], project_exists=True)
    elif g:
        out.update(project=name, project_source="repo")

    # Kontext: Owner → git-Mail → Verzeichnis-Zuordnung → Kontext des Projekts.
    low_owner = owner.lower()
    email = (g.get("email") or "").lower()
    for r in rules:
        if low_owner and str(r.get("owner", "")).lower() == low_owner and r.get("context"):
            out.update(context=r["context"], source="owner")
            break
    if not out["context"] and email:
        for r in rules:
            if email in {str(e).lower() for e in r.get("emails") or []} and r.get("context"):
                out.update(context=r["context"], source="email")
                break
    if not out["context"] and dir_hit is not None and dir_hit.get("context"):
        out.update(context=dir_hit["context"], source="dirs")
    if not out["context"] and proj is not None and proj.get("context"):
        out.update(context=proj["context"], source="project")

    if out["project"] and out["context"]:
        return out
    if not g and dir_hit is None and _is_adhoc(cwd, exact, roots):
        return out  # Ad-hoc-Verzeichnis: nichts zuordnen, nicht fragen
    out["ask"] = True
    if out["project"]:
        out["suggestions"] = [out["project"]]
    else:
        folder = os.path.basename(os.path.normpath(cwd))
        out["suggestions"] = _suggest(folder, projects) + [f"neu: {folder}"]
    return out


# ── Anbindung an den Server (CLI/Extractor) ─────────────────────────────────

def fetch_projects(client) -> list:
    """Aktive Projects mit repos/dirs/keywords; Server < 1.10 → []."""
    try:
        data = json.loads(client.call("memory_project_index", {}) or "[]")
    except Exception:
        return []
    return [p for p in data if isinstance(p, dict) and p.get("name")] if isinstance(data, list) else []


def fetch_rules(client) -> list:
    fetch = getattr(client, "client_config", None)
    cfg = {}
    if callable(fetch):
        try:
            cfg = fetch() or {}
        except Exception:
            cfg = {}
    rules = cfg.get("context_rules")
    return rules if isinstance(rules, list) and rules else DEFAULT_RULES


def resolve_for(client, cwd: str, projects: Optional[list] = None) -> dict:
    if projects is None:
        projects = fetch_projects(client)
    return resolve_origin(cwd, fetch_rules(client), projects)


def ensure_project(client, origin: dict, projects: Optional[list] = None) -> str:
    """Projekt der Herkunft anlegen bzw. das Repo nachtragen. Nur mit erkanntem Repo:
    ohne Repo legt erst die Antwort des Users (map-dir) ein Projekt an.
    Rückgabe: Meldung oder ""."""
    name = origin.get("project")
    repo = origin.get("repo")
    if not name or not repo:
        return ""
    if projects is None:
        projects = fetch_projects(client)
    cur = next((p for p in projects if p.get("name", "").lower() == name.lower()), None)
    if cur is None:
        args = {"name": name, "type": "Project",
                "extra": {"repos": [repo] if "/" in repo else [], "status": "aktiv"}}
        if origin.get("context"):
            args["context"] = origin["context"]
        return client.call("memory_add", args)
    repos = list(cur.get("repos") or [])
    if "/" in repo and repo.lower() not in {r.lower() for r in repos}:
        # extra wird nur flach gemergt → ganze Liste schicken.
        return client.call("memory_add", {"name": cur["name"], "type": "Project",
                                          "extra": {"repos": repos + [repo]}})
    return ""


def map_dir(client, directory: str, project: str, context: str = "") -> str:
    """Verzeichnis dauerhaft einem Project zuordnen (extra.dirs), Kontext setzen,
    Project bei Bedarf anlegen — die Antwort auf die Rückfrage beim Session-Start."""
    d = os.path.abspath(os.path.expanduser(directory))
    projects = fetch_projects(client)
    cur = next((p for p in projects if p.get("name", "").lower() == project.lower()), None)
    dirs = list(cur.get("dirs") or []) if cur else []
    if _norm(d) not in {_norm(x) for x in dirs}:
        dirs.append(d)
    extra = {"dirs": dirs}
    if cur is None:
        extra["status"] = "aktiv"
    args = {"name": cur["name"] if cur else project, "type": "Project", "extra": extra}
    if context:
        args["context"] = context
    return client.call("memory_add", args)

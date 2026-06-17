"""Test für _okf_bundle (Open Knowledge Format v0.1, Google).

Prüft die §9-Konformanz strukturell: jede Nicht-index.md hat YAML-Frontmatter
mit nicht-leerem type; Relationen sind bundle-relative Markdown-Links.
"""
import io
import os
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMPDIR = tempfile.mkdtemp(prefix="ai-rem-okf-")
os.environ["KUZU_DB_PATH"] = os.path.join(_TMPDIR, "kg.db")
os.environ["EMBED_ENABLED"] = "0"
os.environ.setdefault("AI_REM_API_TOKEN", "test-token")

import server  # noqa: E402


def _frontmatter_type(text):
    """Gibt den type-Wert zurück oder None, wenn kein gültiger Frontmatter-Block."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    block = text[4:end]
    for line in block.splitlines():
        if line.startswith("type:"):
            val = line[5:].strip().strip('"')
            return val or None
    return None


def test_okf_bundle_is_conformant():
    server.memory_add("Alice", "Person", description="QA tester\nzweite Zeile",
                      extra={"role": "qa"}, context="work")
    server.memory_add("Proj", "Project", description="ein Projekt")
    server.memory_relate("Alice", "ARBEITET_AN", "Proj")

    z = zipfile.ZipFile(io.BytesIO(server._okf_bundle()))
    names = z.namelist()

    # Root-index.md mit Versionsdeklaration
    assert "index.md" in names
    assert 'okf_version: "0.1"' in z.read("index.md").decode("utf-8")

    # §9: jede Nicht-index.md hat Frontmatter mit nicht-leerem type
    concept_files = [n for n in names if n.endswith(".md") and os.path.basename(n) != "index.md"]
    assert concept_files, "keine Concept-Dateien erzeugt"
    for n in concept_files:
        assert _frontmatter_type(z.read(n).decode("utf-8")), f"{n}: kein gültiger type"

    # Concept-Identität = Pfad; Alice unter person/
    alice = z.read("person/alice.md").decode("utf-8")
    assert _frontmatter_type(alice) == "Person"
    assert "QA tester" in alice
    # bundle-relativer Relationslink auf das Ziel-Concept
    assert "(/project/proj.md)" in alice
    assert "ARBEITET_AN" in alice

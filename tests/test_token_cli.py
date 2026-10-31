"""`ai-rem token list|revoke` ruft die Admin-Tools memory_user_list / memory_token_revoke."""
import importlib.machinery
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _cli():
    loader = importlib.machinery.SourceFileLoader("ai_rem_cli_tok", str(ROOT / "bin" / "ai-rem"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _Fake:
    def __init__(self):
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return "ok"


def _run(mod, argv):
    a = mod.build_parser().parse_args(argv)
    c = _Fake()
    a.func(c, a)
    return c.calls


def test_list_and_revoke():
    mod = _cli()
    assert _run(mod, ["token", "list"]) == [("memory_user_list", None)]
    assert _run(mod, ["token", "revoke", "tokid-1234"]) == [
        ("memory_token_revoke", {"token_id": "tokid-1234", "user": ""})]
    assert _run(mod, ["token", "revoke", "--user", "anna"]) == [
        ("memory_token_revoke", {"token_id": "", "user": "anna"})]
    with pytest.raises(SystemExit):
        _run(mod, ["token", "revoke", "abc"])  # zu kurzes Präfix

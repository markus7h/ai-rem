#!/usr/bin/env python3
"""Graph von einer ai-rem-Instanz in eine andere umziehen (Export → Import).

Gebaut fuer den Wechsel von Kuzu (bis v0.8.32) nach LadybugDB (ab v0.9.0): die
Dateiformate sind nicht kompatibel, LadybugDB weist eine Kuzu-`kg.db` mit
"The file is not a valid Lbug database file!" ab. Der Weg fuehrt deshalb ueber
den JSON-Dump — der wirft nebenbei den angesammelten Kuzu-Ballast ab.

Taugt genauso fuer jeden anderen Umzug (Host-Wechsel, Klon einer Instanz).

Liegt im Image unter /app/scripts/migrate.py. Auf den Host holen:

    docker run --rm --entrypoint cat ghcr.io/markus7h/ai-rem:latest \\
        /app/scripts/migrate.py > migrate.py

Ablauf (Token: `ai-rem token`, oder AI_REM_API_TOKEN setzen):

    # 1) Dump ziehen, solange die ALTE Instanz laeuft
    python3 migrate.py export --url http://localhost:3456 --out dump.json

    # 2) Container stoppen, alte kg.db beiseiteschieben, neues Image starten:
    #    docker compose down
    #    mv /pfad/zum/volume/kg.db /pfad/zum/volume/kg.db.kuzu-alt
    #    docker compose up -d

    # 3) Dump in die NEUE Instanz spielen
    python3 migrate.py import --url http://localhost:3456 --in dump.json

Nur stdlib — laeuft auch im slim-Image und auf jedem Host mit Python 3.10+.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 900  # ein Import mit ein paar tausend Entities rechnet Embeddings mit


def _call(url: str, token: str, path: str, body: bytes | None = None,
          timeout: int = TIMEOUT) -> dict:
    req = urllib.request.Request(url.rstrip("/") + path, data=body, method="POST" if body else "GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def _status(url: str, token: str) -> dict:
    return _call(url, token, "/api/status", timeout=30)


def _warte_auf_gesund(url: str, token: str, sekunden: int) -> None:
    """Nach `docker compose up` braucht der Server ein paar Sekunden (Schema-Migration,
    ggf. Embedding-Modell). Ohne Warteschleife scheitert der Import an ConnectionRefused."""
    ende = time.time() + sekunden
    letzter = ""
    while time.time() < ende:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=5) as r:
                if r.status == 200:
                    return
        except Exception as e:  # noqa: BLE001 — jeder Fehler heisst hier "noch nicht da"
            letzter = str(e)
        time.sleep(2)
    sys.exit(f"FEHLER: {url} wurde in {sekunden}s nicht gesund ({letzter})")


def cmd_export(args) -> None:
    st = _status(args.url, args.token)
    print(f"Quelle: v{st.get('version')} — {st.get('entities')} Entities, "
          f"{st.get('relations')} Relationen")
    dump = _call(args.url, args.token, "/export")
    entities, relations = len(dump.get("entities", [])), len(dump.get("relations", []))
    if not entities:
        sys.exit("FEHLER: Dump enthaelt 0 Entities — nichts geschrieben")
    # Der Dump wird zwischen zwei Queries gebaut; weicht er vom Status ab, hat
    # jemand waehrenddessen geschrieben und der Dump ist nicht der ganze Stand.
    if entities != st.get("entities") or relations != st.get("relations"):
        print(f"WARNUNG: Dump ({entities}/{relations}) weicht vom Status ab — "
              "schreibt noch jemand auf die Instanz?", file=sys.stderr)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False)
    mb = os.path.getsize(args.out) / 1024 / 1024
    print(f"OK: {entities} Entities, {relations} Relationen → {args.out} ({mb:.1f} MB)")
    print("Embeddings sind NICHT im Dump — die neue Instanz rechnet sie beim Import neu.")


def cmd_import(args) -> None:
    with open(args.infile, encoding="utf-8") as f:
        dump = json.load(f)
    entities, relations = len(dump.get("entities", [])), len(dump.get("relations", []))
    if not entities:
        sys.exit(f"FEHLER: {args.infile} enthaelt 0 Entities")

    _warte_auf_gesund(args.url, args.token, args.wait)
    vorher = _status(args.url, args.token)
    if args.mode == "merge" and vorher.get("entities"):
        print(f"Hinweis: Ziel hat schon {vorher['entities']} Entities — "
              "mode=merge laesst sie stehen, mode=replace ersetzt alles.")
    print(f"Ziel: v{vorher.get('version')} — importiere {entities} Entities, "
          f"{relations} Relationen (mode={args.mode})")

    res = _call(args.url, args.token, f"/import?mode={args.mode}",
                json.dumps(dump, ensure_ascii=False).encode())
    print(f"Server: {res}")

    nachher = _status(args.url, args.token)
    fehlend = entities - nachher.get("entities", 0)
    if args.mode == "replace" and fehlend:
        sys.exit(f"FEHLER: Ziel hat {nachher.get('entities')} statt {entities} Entities")
    print(f"OK: {nachher.get('entities')} Entities, {nachher.get('relations')} Relationen, "
          f"kg.db {nachher.get('db_mb')} MB")
    if nachher.get("embed_pending"):
        print(f"{nachher['embed_pending']} Eintraege warten noch auf ihren Vektor — der "
              "Backfill holt sie nach; /api/status zeigt den Fortschritt.")


def _self_check() -> None:
    """Export → Import gegen einen Stub-Server, der die beiden echten Routen nachbildet."""
    import http.server
    import tempfile
    import threading

    graph = {"version": 1, "entities": [{"id": "a", "name": "A", "type": "Topic"}],
             "relations": [{"from_id": "a", "relation": "MAG", "to_id": "a"}]}
    gesehen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def _json(self, obj):
            b = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            gesehen["auth"] = self.headers.get("Authorization")
            if self.path == "/health":
                self.send_response(200), self.end_headers(), self.wfile.write(b"ok")
            elif self.path == "/api/status":
                self._json({"version": "test", "entities": len(graph["entities"]),
                            "relations": len(graph["relations"]), "db_mb": 1.0,
                            "embed_pending": 0})
            else:
                self._json(graph)

        def do_POST(self):
            gesehen["mode"] = self.path
            self.rfile.read(int(self.headers["Content-Length"]))
            self._json({"status": "ok", "entities_created": 1})

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    with tempfile.TemporaryDirectory() as d:
        pfad = os.path.join(d, "dump.json")
        cmd_export(argparse.Namespace(url=url, token="tok", out=pfad))
        assert json.load(open(pfad))["entities"] == graph["entities"]
        assert gesehen["auth"] == "Bearer tok", gesehen
        cmd_import(argparse.Namespace(url=url, token="tok", infile=pfad,
                                      mode="replace", wait=5))
        assert gesehen["mode"] == "/import?mode=replace", gesehen
    srv.shutdown()
    print("self-check OK")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--self-check", action="store_true", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd")

    def gemeinsam(sp):
        sp.add_argument("--url", default="http://localhost:3456", help="Basis-URL der Instanz")
        sp.add_argument("--token", default=os.getenv("AI_REM_API_TOKEN", ""),
                        help="API-Token (Default: $AI_REM_API_TOKEN)")

    e = sub.add_parser("export", help="Graph der laufenden Instanz als JSON sichern")
    gemeinsam(e)
    e.add_argument("--out", default="ai-rem-dump.json")
    e.set_defaults(func=cmd_export)

    i = sub.add_parser("import", help="JSON-Dump in eine Instanz spielen")
    gemeinsam(i)
    i.add_argument("--in", dest="infile", required=True)
    i.add_argument("--mode", choices=["replace", "merge"], default="replace")
    i.add_argument("--wait", type=int, default=300,
                   help="Sekunden auf /health warten (Default 300)")
    i.set_defaults(func=cmd_import)

    args = p.parse_args()
    if args.self_check:
        return _self_check()
    if not getattr(args, "func", None):
        p.print_help()
        sys.exit(2)
    if not args.token:
        sys.exit("FEHLER: kein Token — --token oder AI_REM_API_TOKEN setzen (`ai-rem token`)")
    try:
        args.func(args)
    except urllib.error.HTTPError as ex:
        sys.exit(f"FEHLER: HTTP {ex.code} von {args.url} — {ex.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.URLError as ex:
        sys.exit(f"FEHLER: {args.url} nicht erreichbar — {ex.reason}")


if __name__ == "__main__":
    main()

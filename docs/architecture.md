# Architektur: ai-rem · mykeyvault · tools-registry

Drei zusammengehörige MCP-Systeme für den Claude-Code-Betrieb im LAN:

- **ai-rem** — persistentes Langzeit-Gedächtnis (Knowledge Graph) als FastMCP-Server mit eingebetteter Kuzu-DB.
- **mykeyvault** — Secrets-Verbund: Vaultwarden als Store, `vault-api` als Token-authentifiziertes REST-Gateway, `mykeyvault-mcp` als MCP-Frontend.
- **tools-registry** — lokaler MCP-Server, der Scripts als Tools registriert und sie live von einem zentralen HTTP-`tools-registry` bezieht.

Sie spielen so zusammen: Alle HTTP-MCP-Kanäle laufen über **Caddy** (TLS internal, `*.lan`) auf **mystorage**; authentifiziert wird mit **einem gemeinsamen Bearer-Token** (`ai-rem-api-token`), das ursprünglich aus Vaultwarden stammt und über `vault-api` verteilt wird. **llama-server** läuft separat auf **myubuntu** (GPU, Container `paperless-llama`, geteilt mit paperless-ai) und liefert ai-rem die Transcript-Extraktion und die nächtlichen Cleanup-Urteile. **tools-registry** läuft als stdio-Prozess lokal auf dem Mac und synchronisiert seine Scripts per HTTP vom `tools-registry`.

```mermaid
flowchart TB
  subgraph MAC["Mac - Claude Code Client"]
    CC["Claude Code"]
    HOOKS["Hooks: system-check / auto-memory<br/>claude-md-guard / save-plan"]
    CLI["bin/ai-rem CLI"]
    TMCP["tools-registry (MCP stdio, lokal)"]
    CACHE[("~/.cache/tools-registry/scripts")]
  end

  subgraph UBU["myubuntu - 192.168.2.11"]
    OLLAMA["llama-server (paperless-llama) :11434<br/>mistral-small3.2:24b"]
  end

  subgraph STORAGE["mystorage - 192.168.2.15 (Docker)"]
    CADDY["Caddy - Reverse-Proxy<br/>TLS internal, *.lan"]
    subgraph AIREM_C["Container: ai-rem :3456"]
      AIREM["FastMCP-Server<br/>/mcp · /ui · /api · /health"]
      KUZU[("Kuzu Graph-DB<br/>/data/kg.db + /backups")]
    end
    subgraph KV["mykeyvault-Verbund"]
      KVMCP["mykeyvault-mcp :3458<br/>(MCP http)"]
      VAPI["vault-api :8223 to 8000<br/>FastAPI + bw serve :8087"]
      VW[("Vaultwarden :8222 to 80<br/>Secrets-Store")]
    end
    REG["tools-registry :3457<br/>HTTP Script-Verteiler<br/>/registry · /registry/file"]
  end

  %% MCP-Kanaele (Bearer-Token ai-rem-api-token)
  CC -- "MCP https://airem.lan/mcp" --> CADDY
  CC -- "MCP https://mykeyvault.lan/mcp" --> CADDY
  CADDY -- "Bearer-Token" --> AIREM
  CADDY -- "Bearer-Token" --> KVMCP
  CADDY -. "https://mykeyvault.lan/secret/* u. a. (pfadbasiert)" .-> VAPI
  CADDY -. "https://mykeyvault.lan (Rest)" .-> VW

  %% tools-registry lokal
  CC --> TMCP
  TMCP -- "Poll /registry (HTTP :3457)" --> REG
  TMCP --> CACHE
  TMCP -. "ai_rem_token: liest ~/.claude.json" .-> CC

  %% Hooks und CLI
  HOOKS -- "auto-memory to ai-rem ingest" --> CADDY
  CLI --> CADDY

  %% ai-rem Abhaengigkeiten
  AIREM --> KUZU
  AIREM -- "Extraktion + Nightly-Cleanup" --> OLLAMA

  %% Secrets-Fluss
  KVMCP -- "http://vault-api:8000 (intern)" --> VAPI
  VAPI -- "bw serve :8087" --> VW
  AIREM -. "Token-Quelle: Item ai-rem-api-token" .-> VAPI
```

## Komponenten

| Komponente | Host | Port | Protokoll / Zugang | Zweck |
|---|---|---|---|---|
| ai-rem | mystorage | 3456 | HTTP-MCP via `https://airem.lan/mcp` (Caddy), Bearer | Knowledge-Graph-Gedächtnis (FastMCP + Kuzu), Web-UI, Backup/Cleanup |
| Kuzu Graph-DB | mystorage | — | eingebettet in ai-rem | Entities/Relations (`/data/kg.db`), Backups (`/backups`) |
| mykeyvault-mcp | mystorage | 3458 | HTTP-MCP via `https://mykeyvault.lan/mcp` (Caddy, pfadbasiert), Bearer | MCP-Frontend für die Vault-Tools (kein Secret-Leak in den Kontext) |
| vault-api | mystorage | 8223→8000 | REST via `https://mykeyvault.lan` (Caddy, pfadbasiert: `/secret/*`, `/items*`, `/item/*`, `/ssh-key/*`, `/ssh-keys`, `/health`), Bearer | Token-Gateway um die Bitwarden-CLI; hält `bw serve` (`:8087`) entsperrt |
| Vaultwarden | mystorage | 8222→80 | `https://mykeyvault.lan` (Caddy, alle übrigen Pfade) | Eigentlicher Secrets-Store |
| tools-registry | mystorage | 3457 | reines HTTP (LAN-only, keine Auth) | Verteilt die Scripts (`/registry`, `/registry/file`) |
| tools-registry | Mac (lokal) | — | MCP stdio (Node-Prozess) | Registriert Scripts als Tools; pollt den Registry alle 5 s |
| llama-server | myubuntu | 11434 | HTTP (OpenAI-kompatibel) | Transcript-Extraktion + Nightly-Cleanup-Urteile für ai-rem (Container `paperless-llama`, geteilt mit paperless-ai) |
| Caddy | mystorage | — | Reverse-Proxy, `tls internal` | Terminiert TLS für alle `*.lan`-Endpunkte |

**Auth:** ai-rem und mykeyvault-mcp teilen sich denselben Bearer-Token (`ai-rem-api-token`, als `MCP_AUTH_TOKEN`); `vault-api` verwendet ihn als `VAULT_API_TOKEN`. Der Token stammt aus Vaultwarden und wird über `vault-api` an die Clients verteilt; ai-rem frischt ihn pro Session im Hintergrund auf.

> **Hinweis:** `mykeyvault-mcp` läuft produktiv als HTTP-MCP-Container (`:3458`, `https://mykeyvault.lan/mcp`). Der Repo-Code (`mcp/src/index.ts`) zeigt noch die ältere stdio-Variante — das Repo hängt hier hinter der Produktion. Dieses Diagramm bildet die deployte Realität ab.
>
> **Hostnamen-Konvention (seit 2026-06-11):** Alles, was zu mykeyvault gehört, läuft unter dem einen Hostnamen `mykeyvault.lan` mit pfadbasiertem Caddy-Routing (`/mcp*` → mykeyvault-mcp, vault-api-Pfade → vault-api, Rest → Vaultwarden). Die früheren Spezial-Hostnamen `keyvault-mcp.lan` und `keyvault-api.lan` sind abgeschafft.

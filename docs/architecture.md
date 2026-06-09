# Architektur: ai-rem · mykeyvault · tools-mcp

Drei zusammengehörige MCP-Systeme für den Claude-Code-Betrieb im LAN:

- **ai-rem** — persistentes Langzeit-Gedächtnis (Knowledge Graph) als FastMCP-Server mit eingebetteter Kuzu-DB.
- **mykeyvault** — Secrets-Verbund: Vaultwarden als Store, `vault-api` als Token-authentifiziertes REST-Gateway, `mykeyvault-mcp` als MCP-Frontend.
- **tools-mcp** — lokaler MCP-Server, der Scripts als Tools registriert und sie live von einem zentralen HTTP-`tools-registry` bezieht.

Sie spielen so zusammen: Alle HTTP-MCP-Kanäle laufen über **Caddy** (TLS internal, `*.lan`) auf **mystorage**; authentifiziert wird mit **einem gemeinsamen Bearer-Token** (`ai-rem-api-token`), das ursprünglich aus Vaultwarden stammt und über `vault-api` verteilt wird. **Ollama** läuft separat auf **myubuntu** (GPU) und liefert ai-rem die Transcript-Extraktion und die nächtlichen Cleanup-Urteile. **tools-mcp** läuft als stdio-Prozess lokal auf dem Mac und synchronisiert seine Scripts per HTTP vom `tools-registry`.

```mermaid
flowchart TB
  subgraph MAC["Mac - Claude Code Client"]
    CC["Claude Code"]
    HOOKS["Hooks: system-check / auto-memory<br/>claude-md-guard / save-plan"]
    CLI["bin/ai-rem CLI"]
    TMCP["tools-mcp (MCP stdio, lokal)"]
    CACHE[("~/.cache/tools-mcp/scripts")]
  end

  subgraph UBU["myubuntu - 192.168.2.11"]
    OLLAMA["Ollama :11434<br/>qwen3:14b / mistral-small"]
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
  CC -- "MCP https://keyvault-mcp.lan/mcp" --> CADDY
  CADDY -- "Bearer-Token" --> AIREM
  CADDY -- "Bearer-Token" --> KVMCP
  CADDY -. "https://keyvault-api.lan" .-> VAPI
  CADDY -. "https://mykeyvault.lan" .-> VW

  %% tools-mcp lokal
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
| mykeyvault-mcp | mystorage | 3458 | HTTP-MCP via `https://keyvault-mcp.lan/mcp` (Caddy), Bearer | MCP-Frontend für die Vault-Tools (kein Secret-Leak in den Kontext) |
| vault-api | mystorage | 8223→8000 | REST via `https://keyvault-api.lan` (Caddy), Bearer | Token-Gateway um die Bitwarden-CLI; hält `bw serve` (`:8087`) entsperrt |
| Vaultwarden | mystorage | 8222→80 | `https://mykeyvault.lan` (Caddy) | Eigentlicher Secrets-Store |
| tools-registry | mystorage | 3457 | reines HTTP (LAN-only, keine Auth) | Verteilt die Scripts (`/registry`, `/registry/file`) |
| tools-mcp | Mac (lokal) | — | MCP stdio (Node-Prozess) | Registriert Scripts als Tools; pollt den Registry alle 5 s |
| Ollama | myubuntu | 11434 | HTTP | Transcript-Extraktion + Nightly-Cleanup-Urteile für ai-rem |
| Caddy | mystorage | — | Reverse-Proxy, `tls internal` | Terminiert TLS für alle `*.lan`-Endpunkte |

**Auth:** ai-rem und mykeyvault-mcp teilen sich denselben Bearer-Token (`ai-rem-api-token`, als `MCP_AUTH_TOKEN`); `vault-api` verwendet ihn als `VAULT_API_TOKEN`. Der Token stammt aus Vaultwarden und wird über `vault-api` an die Clients verteilt; ai-rem frischt ihn pro Session im Hintergrund auf.

> **Hinweis:** `mykeyvault-mcp` läuft produktiv als HTTP-MCP-Container (`:3458`, `https://keyvault-mcp.lan`). Der Repo-Code (`mcp/src/index.ts`) zeigt noch die ältere stdio-Variante — das Repo hängt hier hinter der Produktion. Dieses Diagramm bildet die deployte Realität ab.

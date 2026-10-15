// ai-rem für opencode — Gegenstück zu den Claude-Code-Hooks auto-memory.py und
// system-check.py. Ausgeliefert vom ai-rem-Server (/clients/opencode/ai-rem.ts),
// installiert von `ai-rem install --client opencode` nach
// ~/.config/opencode/plugin/ai-rem.ts. Lokale Änderungen überschreibt `ai-rem update`.
//
// - session.created: verpasste Ingests nachziehen (ai-rem catchup) und einmal pro
//   Prozess Client-Drift prüfen (ai-rem update --check → Toast, kein Auto-Update).
// - session.idle: nach IDLE_MS ohne weitere Antwort die Session exportieren und per
//   `ai-rem ingest` ins Gedächtnis extrahieren. Nur wenn seit dem letzten Ingest neue
//   Nachrichten dazugekommen sind.
// - session.compacted: sofort exportieren, bevor der Kontext weg ist.
// - Subagent-Sessions (parentID gesetzt) werden nie extrahiert: Sie sehen nur den
//   Auftrag ("sende jetzt die Mail"), nicht das Ergebnis — daraus wurden offene Tasks
//   fuer laengst erledigte Arbeit. Die Eltern-Session enthaelt alles Relevante.
//
// Zwei API-Generationen aus EINER Datei (ohne Imports/node_modules):
//   opencode 2.x  → default.setup(ctx): Events per ctx.event.subscribe(), Nachrichten
//                   per ctx.session.context() (nur seit der letzten Compaction!)
//   opencode 1.x  → default.server({ client }): Hook "event", client.session.messages()
// Beide Wege normalisieren auf dasselbe Export-Format ({info:{role}, parts:[…]}), das
// lib/extractor.py liest — Server und Extractor bleiben unberuehrt.
import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync, existsSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

const IDLE_MS = 10 * 60 * 1000
const HOME = homedir()
const CLI = join(HOME, ".local", "share", "ai-rem", "bin", "ai-rem")
const OUT_DIR = join(HOME, ".local", "share", "ai-rem", "transcripts")

function runCli(args: string[]): void {
  if (!existsSync(CLI)) return
  const cmd = process.platform === "win32" ? "python" : CLI
  const argv = process.platform === "win32" ? ["-X", "utf8", CLI, ...args] : args
  try {
    const child = spawn(cmd, argv, {
      detached: true,
      stdio: "ignore",
      env: { ...process.env, AI_REM_CLIENT: "opencode" },
    })
    child.unref()
  } catch {
    // CLI nicht startbar: still bleiben, opencode darf nicht hängen.
  }
}

function checkUpdate(): Promise<boolean> {
  return new Promise((resolve) => {
    if (!existsSync(CLI)) return resolve(false)
    const cmd = process.platform === "win32" ? "python" : CLI
    const argv = process.platform === "win32" ? ["-X", "utf8", CLI, "update", "--check"] : ["update", "--check"]
    try {
      const child = spawn(cmd, argv, { stdio: "ignore" })
      child.on("exit", (code) => resolve(code === 1))
      child.on("error", () => resolve(false))
    } catch {
      resolve(false)
    }
  })
}

// ── Normalisierung ───────────────────────────────────────────────────────────

type Part = { type: string; text: string }
type Msg = { info: { role: "user" | "assistant" }; parts: Part[] }

// v2: {type:"user", text} | {type:"assistant", content:[{type:"text"|"reasoning"|tool…}]}
// v1: {info:{role}, parts:[…]} (unveraendert durchreichen). Tool-Aufrufe, synthetic,
// system, shell, compaction u.a. entfallen — der Extractor wuerde sie ohnehin verwerfen.
function normalize(raw: any[]): Msg[] {
  const out: Msg[] = []
  for (const m of raw ?? []) {
    if (m?.info?.role) {
      out.push(m)
    } else if (m?.type === "user") {
      out.push({ info: { role: "user" }, parts: [{ type: "text", text: String(m.text ?? "") }] })
    } else if (m?.type === "assistant") {
      const parts: Part[] = []
      for (const c of m.content ?? []) {
        if ((c?.type === "text" || c?.type === "reasoning") && c.text) parts.push({ type: c.type, text: c.text })
      }
      out.push({ info: { role: "assistant" }, parts })
    }
  }
  return out
}

// ── Gemeinsamer Kern ─────────────────────────────────────────────────────────

// cwd: Arbeitsverzeichnis der Instanz — daraus leitet der Extractor Projekt (Repo) und
// Kontext (git-Remote/-Mail) ab, statt das LLM raten zu lassen (lib/origin.py).
function createCore(getMessages: (sessionID: string) => Promise<any[]>, cwd: string) {
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const ingested = new Map<string, number>() // sessionID → Nachrichtenzahl beim letzten Ingest
  const segment = new Map<string, number>() // sessionID → Compaction-Zaehler (eigene Datei je Abschnitt)
  const children = new Set<string>() // Subagent-Sessions — nie ingesten
  let checked = false

  // rotate: Compaction steht bevor — danach beginnt der Kontext von vorn, also neuer
  // Abschnitt (andere Datei, Zaehler zurueck), sonst ueberschriebe er den alten.
  async function ingest(sessionID: string, rotate = false): Promise<void> {
    clearTimeout(timers.get(sessionID))
    timers.delete(sessionID)
    try {
      const messages = normalize(await getMessages(sessionID))
      const seg = segment.get(sessionID) ?? 0
      if (messages.length > (ingested.get(sessionID) ?? 0)) {
        mkdirSync(OUT_DIR, { recursive: true })
        const file = join(OUT_DIR, `opencode-${sessionID}${seg ? `-c${seg}` : ""}.json`)
        writeFileSync(file, JSON.stringify({ format: "opencode", session: sessionID, cwd, messages }))
        ingested.set(sessionID, messages.length)
        runCli(["ingest", "--transcript", file])
      }
      if (rotate) {
        segment.set(sessionID, seg + 1)
        ingested.set(sessionID, 0)
      }
    } catch {
      // Export fehlgeschlagen — beim naechsten idle erneut versuchen.
    }
  }

  return {
    // Session wurde angelegt: offene Timer jetzt ingesten (opencode kann beendet werden,
    // bevor sie ablaufen), verpasste Ingests nachziehen, einmal pro Prozess Drift pruefen.
    // Subagent-Start: nichts flushen — sonst landet die Eltern-Session mitten im Turn
    // im Extractor, und jeder weitere Ingest derselben Session kann Erledigtes erneut
    // als offenen Task anlegen.
    async created(notify: (msg: string) => Promise<void>, sessionID?: string, parentID?: string): Promise<void> {
      if (parentID) {
        if (sessionID) children.add(sessionID)
        return
      }
      for (const other of [...timers.keys()]) void ingest(other)
      runCli(["catchup"])
      if (!checked) {
        checked = true
        if (await checkUpdate()) await notify("ai-rem: Client veraltet — `ai-rem update` ausführen")
      }
    },
    idle(sessionID: string): void {
      if (children.has(sessionID)) return
      clearTimeout(timers.get(sessionID))
      timers.set(sessionID, setTimeout(() => void ingest(sessionID), IDLE_MS))
    },
    compaction: async (sessionID: string, rotate: boolean) => {
      if (!children.has(sessionID)) await ingest(sessionID, rotate)
    },
    // Beenden vor Ablauf des Timers: letzten Stand noch exportieren statt verwerfen.
    // Best effort — opencode wartet nicht zwingend; was fehlt, holt `catchup` nicht nach.
    dispose(): void {
      for (const sid of [...timers.keys()]) void ingest(sid)
    },
  }
}

// ── opencode 2.x ─────────────────────────────────────────────────────────────

// Turn beendet (egal wie) = "idle". session.idle gibt es in v2 nur noch als Altlast.
const V2_IDLE = new Set([
  "session.idle",
  "session.execution.succeeded",
  "session.execution.failed",
  "session.execution.interrupted",
])

async function setup(ctx: any): Promise<() => void> {
  // v2: ctx.location.directory (2.0.26 geprueft); defensiv mit Fallbacks.
  const cwd: string = ctx?.location?.directory ?? ctx?.directory ?? ctx?.worktree ?? process.cwd()
  const core = createCore(async (sessionID) => (await ctx.session.context({ sessionID })) ?? [], cwd)
  const controller = new AbortController()

  // v2 hat keine Toast-API fuer Plugins: Hinweis nur ins Log.
  const notify = async (msg: string) => console.warn(msg)

  async function handle(event: any): Promise<void> {
    const d = event?.data ?? event?.properties ?? {}
    const sid: string | undefined = d.sessionID ?? d.info?.id
    const type: string | undefined = event?.type
    if (!type) return
    if (type === "session.created") await core.created(notify, sid, d.info?.parentID ?? d.parentID)
    else if (!sid) return
    else if (V2_IDLE.has(type)) core.idle(sid)
    // started: der Kontext ist noch vollstaendig → jetzt sichern, danach neuer Abschnitt.
    else if (type === "session.compaction.started") await core.compaction(sid, true)
  }

  // Der Eventstream ist "volatile": ein langsamer Konsument fliegt raus. Neu verbinden.
  void (async () => {
    let delay = 1000
    while (!controller.signal.aborted) {
      try {
        for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
          delay = 1000
          void handle(event).catch(() => {})
        }
      } catch {
        // Stream abgerissen
      }
      if (controller.signal.aborted) break
      await new Promise((r) => setTimeout(r, delay))
      delay = Math.min(delay * 2, 30000)
    }
  })()

  return () => {
    controller.abort()
    core.dispose()
  }
}

// ── opencode 1.x ─────────────────────────────────────────────────────────────

async function server({ client, directory, worktree }: { client: any; directory?: string; worktree?: string }) {
  const core = createCore(
    async (sessionID) => (await client.session.messages({ path: { id: sessionID } }))?.data ?? [],
    directory ?? worktree ?? process.cwd(),
  )
  const notify = async (message: string) => {
    await client.tui?.showToast?.({ body: { message, variant: "warning" } }).catch?.(() => {})
  }
  return {
    event: async ({ event }: { event: any }) => {
      const sid: string | undefined = event?.properties?.sessionID ?? event?.properties?.info?.id
      switch (event?.type) {
        case "session.created":
          await core.created(notify, sid, event?.properties?.info?.parentID)
          break
        case "session.idle":
          if (sid) core.idle(sid)
          break
        case "session.compacted":
          if (sid) await core.compaction(sid, false)
          break
      }
    },
  }
}

// opencode 2.x verlangt einen Default-Export mit id + setup; 1.18.29+ ruft server().
export default { id: "ai-rem", setup, server }

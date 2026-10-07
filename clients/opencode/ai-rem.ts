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
//
// Keine Imports aus @opencode-ai/plugin: das Plugin soll ohne node_modules laufen.
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

export const AiRem = async ({ client }: { client: any }) => {
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const ingested = new Map<string, number>() // sessionID → Nachrichtenzahl beim letzten Ingest
  let checked = false

  async function ingest(sessionID: string): Promise<void> {
    timers.delete(sessionID)
    try {
      const res = await client.session.messages({ path: { id: sessionID } })
      const messages = res?.data ?? []
      if (messages.length <= (ingested.get(sessionID) ?? 0)) return
      mkdirSync(OUT_DIR, { recursive: true })
      const file = join(OUT_DIR, `opencode-${sessionID}.json`)
      writeFileSync(file, JSON.stringify({ format: "opencode", session: sessionID, messages }))
      ingested.set(sessionID, messages.length)
      runCli(["ingest", "--transcript", file])
    } catch {
      // Export fehlgeschlagen — beim nächsten idle erneut versuchen.
    }
  }

  return {
    event: async ({ event }: { event: any }) => {
      const sid: string | undefined = event?.properties?.sessionID ?? event?.properties?.info?.id
      switch (event?.type) {
        case "session.created":
          // Andere Sessions mit offenem Timer jetzt ingesten: opencode kann
          // beendet werden, bevor ihr Timer abläuft.
          for (const other of [...timers.keys()]) {
            clearTimeout(timers.get(other))
            void ingest(other)
          }
          runCli(["catchup"])
          if (!checked) {
            checked = true
            if (await checkUpdate()) {
              await client.tui?.showToast?.({
                body: { message: "ai-rem: Client veraltet — `ai-rem update` ausführen", variant: "warning" },
              }).catch?.(() => {})
            }
          }
          break
        case "session.idle":
          if (!sid) break
          clearTimeout(timers.get(sid))
          timers.set(sid, setTimeout(() => void ingest(sid), IDLE_MS))
          break
        case "session.compacted":
          if (!sid) break
          clearTimeout(timers.get(sid))
          await ingest(sid)
          break
      }
    },
  }
}

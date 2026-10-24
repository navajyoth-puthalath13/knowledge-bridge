// Supervises the local Python service: start, health-check, restart on crash,
// graceful stop. The per-session token lives only in the main process.
import { ChildProcess, spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";

export type BackendState = "stopped" | "starting" | "running" | "crashed";

export interface BackendInfo {
  state: BackendState;
  error: string | null;
  port: number | null;
  python: string;
  backendDir: string;
  logs: string[];
  restarts: number;
}

const MAX_LOG_LINES = 300;
const MAX_AUTO_RESTARTS = 3;

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address();
      srv.close(() => (typeof addr === "object" && addr ? resolve(addr.port) : reject(new Error("no port"))));
    });
  });
}

export class Backend {
  private proc: ChildProcess | null = null;
  private token = "";
  private stopping = false;
  private listeners: Array<(info: BackendInfo) => void> = [];
  info: BackendInfo;

  constructor(backendDir: string, python: string) {
    this.info = { state: "stopped", error: null, port: null, python, backendDir, logs: [], restarts: 0 };
  }

  onChange(fn: (info: BackendInfo) => void) {
    this.listeners.push(fn);
  }

  private set(patch: Partial<BackendInfo>) {
    Object.assign(this.info, patch);
    for (const fn of this.listeners) fn(this.info);
  }

  private log(line: string) {
    const lines = line.split(/\r?\n/).filter(Boolean);
    this.info.logs.push(...lines);
    if (this.info.logs.length > MAX_LOG_LINES) this.info.logs.splice(0, this.info.logs.length - MAX_LOG_LINES);
  }

  async start(): Promise<void> {
    if (this.proc) return;
    const { python, backendDir } = this.info;
    if (!fs.existsSync(python)) {
      this.set({
        state: "crashed",
        error: `Python environment not found at ${python}. Run scripts/setup.sh in the repository first.`,
      });
      return;
    }
    this.stopping = false;
    this.token = crypto.randomBytes(32).toString("base64url");
    const port = await freePort();
    this.set({ state: "starting", error: null, port });
    const env: NodeJS.ProcessEnv = { ...process.env, KB_TOKEN: this.token, PYTHONUNBUFFERED: "1" };
    delete env.NODE_OPTIONS;
    const proc = spawn(python, ["-m", "app.main", "--port", String(port)], {
      cwd: backendDir,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    this.proc = proc;
    proc.stdout?.on("data", (d) => this.log(String(d)));
    proc.stderr?.on("data", (d) => this.log(String(d)));
    proc.on("exit", (code, signal) => {
      this.proc = null;
      if (this.stopping) {
        this.set({ state: "stopped", port: null });
        return;
      }
      const err = `Service exited (${signal ?? `code ${code}`}). ${this.info.logs.slice(-3).join(" ")}`;
      this.set({ state: "crashed", error: err, port: null });
      if (this.info.restarts < MAX_AUTO_RESTARTS) {
        this.info.restarts += 1;
        setTimeout(() => void this.start(), 1000 * this.info.restarts);
      }
    });
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline && this.proc === proc) {
      try {
        const r = await fetch(`http://127.0.0.1:${port}/health`);
        if (r.ok) {
          this.set({ state: "running", error: null });
          return;
        }
      } catch {
        /* not up yet */
      }
      await new Promise((r) => setTimeout(r, 250));
    }
    if (this.proc === proc) {
      this.set({ state: "crashed", error: "Service did not become healthy within 30 seconds." });
      proc.kill("SIGKILL");
    }
  }

  async stop(): Promise<void> {
    const proc = this.proc;
    if (!proc) return;
    this.stopping = true;
    proc.kill("SIGTERM"); // uvicorn shuts down gracefully and lets the running sync stop at a safe point
    await new Promise<void>((resolve) => {
      const t = setTimeout(() => {
        proc.kill("SIGKILL");
        resolve();
      }, 10_000);
      proc.on("exit", () => {
        clearTimeout(t);
        resolve();
      });
    });
  }

  async restart(): Promise<void> {
    await this.stop();
    this.info.restarts = 0;
    await this.start();
  }

  async request(method: string, urlPath: string, body?: unknown): Promise<{ ok: boolean; status: number; data: unknown }> {
    if (this.info.state !== "running" || !this.info.port) {
      return { ok: false, status: 0, data: { detail: "The local service is not running." } };
    }
    try {
      const r = await fetch(`http://127.0.0.1:${this.info.port}${urlPath}`, {
        method,
        headers: { Authorization: `Bearer ${this.token}`, "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const text = await r.text();
      let data: unknown = text;
      try {
        data = JSON.parse(text);
      } catch {
        /* plain text */
      }
      return { ok: r.ok, status: r.status, data };
    } catch (e) {
      return { ok: false, status: 0, data: { detail: `Could not reach the local service: ${String(e)}` } };
    }
  }
}

export function resolveBackendDir(appPath: string, isPackaged: boolean, resourcesPath: string): string {
  if (process.env.KB_BACKEND_DIR) return process.env.KB_BACKEND_DIR;
  if (isPackaged) {
    // Personal build: the app runs the backend from the repository it was built from.
    const marker = path.join(resourcesPath, "backend-path.txt");
    if (fs.existsSync(marker)) return fs.readFileSync(marker, "utf8").trim();
  }
  return path.resolve(appPath, "..", "..", "backend");
}

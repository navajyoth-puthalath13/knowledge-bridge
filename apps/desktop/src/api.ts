// Typed access to the local service through the preload bridge.

export interface BackendInfo {
  state: "stopped" | "starting" | "running" | "crashed";
  error: string | null;
  port: number | null;
  python: string;
  backendDir: string;
  logs?: string[];
  restarts: number;
}

interface Bridge {
  api(method: string, path: string, body?: unknown): Promise<{ ok: boolean; status: number; data: any }>;
  backendInfo(): Promise<BackendInfo>;
  restartBackend(): Promise<BackendInfo>;
  onBackendChange(fn: (info: BackendInfo) => void): () => void;
  pickFolder(title: string): Promise<string | null>;
  openObsidian(): Promise<boolean>;
  openFolder(which: "vault" | "generated" | "mirror"): Promise<boolean>;
  loginItem(enable?: boolean): Promise<{ supported: boolean; enabled: boolean }>;
}

declare global {
  interface Window {
    kb: Bridge;
  }
}

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

function detail(data: any): string {
  if (!data) return "Request failed";
  if (typeof data === "string") return data;
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map((d: any) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
  return JSON.stringify(data);
}

export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await window.kb.api(method, path, body);
  if (!r.ok) throw new ApiError(detail(r.data), r.status);
  return r.data as T;
}

export const get = <T = any>(p: string) => api<T>("GET", p);
export const post = <T = any>(p: string, b: unknown = {}) => api<T>("POST", p, b);
export const put = <T = any>(p: string, b: unknown) => api<T>("PUT", p, b);
export const patch = <T = any>(p: string, b: unknown) => api<T>("PATCH", p, b);

export interface RunStats {
  discovered: number; created: number; updated: number; renamed: number; unchanged: number; skipped: number;
  missing: number; locked: number; conflicts: number; processed: number; llm_calls: number;
  entities_created: number; entities_reused: number; relationships: number; reviews: number;
  pages_written: number; errors: number; mirrored: number;
}

export interface RunIssue { note: string; note_id?: string | null; message: string; kind: "error" | "conflict" | "warning" }
export interface PlanItem { note: string; action: string; path?: string; from?: string; detail?: string; warnings?: string[] }

export interface Report {
  run_id: number | null; dry_run: boolean; status: string; stats: RunStats; errors: RunIssue[]; plan: PlanItem[];
}

export interface Status {
  version: string;
  notice: string | null;
  connector: { ok: boolean; message: string; kind: string; folder: string };
  vault: { ok: boolean; path: string | null; mirror: string; generated: string; known_to_obsidian: boolean | null };
  provider: { name: string; model: string; cloud: boolean; free: boolean; key_set: boolean | null; key_storage: string; ready: boolean };
  problems: string[];
  counts: { notes: number; notes_total: number; entities: number; relationships: number; reviews: number; conflicts: number; errors: number };
  last_sync: { finished_at: string; status: string } | null;
  activity: { running: boolean; phase?: string; current?: number; total?: number; message?: string; dry_run?: boolean; trigger?: string; started_at?: string };
  last_report: Report | null;
  automation: { enabled: boolean; interval_minutes: number; next_run_at: string | null; scheduler_alive: boolean };
}

export interface Config {
  vault_path: string;
  mirror_dir: string;
  generated_dir: string;
  obsidian_vault_name: string;
  source: { kind: "apple_notes" | "folder"; account: string; folder: string; include_subfolders: boolean; folder_path: string; export_attachments: boolean };
  provider: { name: "none" | "ollama" | "gemini" | "groq" | "openai" | "xai" | "mock"; model: string; base_url: string; temperature: number; timeout_seconds: number };
  intelligence: { auto_accept_confidence: number; fuzzy_auto_merge: number; fuzzy_review: number; semantic_suggestions: boolean; semantic_auto_accept: boolean; enabled_types: Record<string, boolean> };
  sync_interval_minutes: number;
  automation_enabled: boolean;
  mirror_frontmatter: boolean;
}

export const TYPE_LABEL: Record<string, string> = {
  book: "Book", podcast_show: "Podcast show", podcast_episode: "Podcast episode", person: "Person",
  event_series: "Event series", event: "Event", topic: "Topic", note: "Note", collection: "Collection",
};

export function ago(iso?: string | null): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 0) {
    const f = -s;
    if (f < 60) return "in under a minute";
    if (f < 3600) return `in ${Math.round(f / 60)} min`;
    return `in ${Math.round(f / 3600)} h`;
  }
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleString();
}

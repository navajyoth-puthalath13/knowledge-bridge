import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import path from "node:path";
import { Backend, resolveBackendDir } from "./backend";

// Only these API paths can be reached from the renderer.
const ALLOWED = [
  /^\/status$/, /^\/config$/, /^\/secrets\/(gemini|groq|openai|xai)$/, /^\/provider\/test$/, /^\/source\/folders$/,
  /^\/sync(\/cancel)?$/, /^\/automation$/, /^\/rebuild$/, /^\/runs(\?limit=\d+)?$/, /^\/notes$/,
  /^\/notes\/reprocess$/, /^\/notes\/[^?#]+\/reattach$/, /^\/entity-types(\/\w+)?$/,
  /^\/entities(\?[\w=&%.\-+]*)?$/, /^\/entities\/[\w-]+(\/merge)?$/, /^\/relationships(\?[\w=&%.\-]*)?$/,
  /^\/reviews(\?status=\w+)?$/, /^\/reviews\/\d+\/resolve$/, /^\/conflicts(\?status=\w+)?$/,
  /^\/conflicts\/\d+\/resolve$/, /^\/diagnostics$/,
];
const METHODS = new Set(["GET", "POST", "PUT", "PATCH"]);

const backendDir = resolveBackendDir(app.getAppPath(), app.isPackaged, process.resourcesPath);
const python = process.env.KB_PYTHON || path.join(backendDir, ".venv", "bin", "python");
const backend = new Backend(backendDir, python);
let win: BrowserWindow | null = null;

function createWindow() {
  win = new BrowserWindow({
    width: 1180,
    height: 780,
    minWidth: 900,
    minHeight: 600,
    title: "Knowledge Bridge",
    backgroundColor: "#f6f6f4",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: false,
    },
  });
  // No navigation away from the app and no new windows; external https links open in the browser.
  win.webContents.on("will-navigate", (e) => e.preventDefault());
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("https://")) void shell.openExternal(url);
    return { action: "deny" };
  });
  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) void win.loadURL(devUrl);
  else void win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  win.on("closed", () => (win = null));
}

backend.onChange((info) => win?.webContents.send("backend:changed", { ...info, logs: undefined }));

async function config(): Promise<Record<string, any> | null> {
  const r = await backend.request("GET", "/config");
  return r.ok ? (r.data as Record<string, any>) : null;
}

ipcMain.handle("api", async (_e, method: string, urlPath: string, body?: unknown) => {
  if (typeof method !== "string" || !METHODS.has(method) || typeof urlPath !== "string" || urlPath.includes("..")
      || !ALLOWED.some((re) => re.test(urlPath))) {
    return { ok: false, status: 400, data: { detail: "request not allowed" } };
  }
  return backend.request(method, urlPath, body);
});

ipcMain.handle("backend:info", () => backend.info);
ipcMain.handle("backend:restart", async () => {
  await backend.restart();
  return backend.info;
});

ipcMain.handle("dialog:pickFolder", async (_e, title: unknown) => {
  if (!win) return null;
  const r = await dialog.showOpenDialog(win, {
    title: typeof title === "string" ? title.slice(0, 100) : "Choose a folder",
    properties: ["openDirectory", "createDirectory"],
  });
  return r.canceled ? null : r.filePaths[0];
});

ipcMain.handle("open:obsidian", async () => {
  const cfg = await config();
  if (!cfg?.vault_path) return false;
  const name = cfg.obsidian_vault_name || path.basename(cfg.vault_path);
  await shell.openExternal(`obsidian://open?vault=${encodeURIComponent(name)}`);
  return true;
});

ipcMain.handle("open:folder", async (_e, which: unknown) => {
  const cfg = await config();
  if (!cfg?.vault_path) return false;
  const target = which === "generated" ? path.join(cfg.vault_path, cfg.generated_dir)
    : which === "mirror" ? path.join(cfg.vault_path, cfg.mirror_dir)
    : which === "vault" ? cfg.vault_path : null;
  if (!target) return false;
  return (await shell.openPath(target)) === "";
});

ipcMain.handle("app:loginItem", (_e, enable?: unknown) => {
  if (process.platform !== "darwin") return { supported: false, enabled: false };
  if (typeof enable === "boolean") app.setLoginItemSettings({ openAtLogin: enable });
  return { supported: true, enabled: app.getLoginItemSettings().openAtLogin };
});

app.whenReady().then(async () => {
  createWindow();
  await backend.start();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

// On macOS closing the window keeps the agent running (scheduled sync continues) until you quit.
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

let quitting = false;
app.on("before-quit", (e) => {
  if (quitting) return;
  e.preventDefault();
  quitting = true;
  void backend.stop().finally(() => app.exit(0));
});

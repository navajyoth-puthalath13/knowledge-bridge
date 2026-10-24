// The only bridge between the renderer and the system. It exposes a handful of
// named operations; there is no generic filesystem, shell or IPC access.
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("kb", {
  api: (method: string, path: string, body?: unknown) => ipcRenderer.invoke("api", method, path, body),
  backendInfo: () => ipcRenderer.invoke("backend:info"),
  restartBackend: () => ipcRenderer.invoke("backend:restart"),
  onBackendChange: (fn: (info: unknown) => void) => {
    const handler = (_e: unknown, info: unknown) => fn(info);
    ipcRenderer.on("backend:changed", handler);
    return () => ipcRenderer.removeListener("backend:changed", handler);
  },
  pickFolder: (title: string) => ipcRenderer.invoke("dialog:pickFolder", title),
  openObsidian: () => ipcRenderer.invoke("open:obsidian"),
  openFolder: (which: "vault" | "generated" | "mirror") => ipcRenderer.invoke("open:folder", which),
  loginItem: (enable?: boolean) => ipcRenderer.invoke("app:loginItem", enable),
});

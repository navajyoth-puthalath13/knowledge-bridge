import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  base: "./", // loaded from file:// in production
  server: { port: 5173, strictPort: true, host: "127.0.0.1" },
  build: { outDir: "dist", emptyOutDir: true },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The conversational app. Dev: proxies the engine API on :8000. Build: served by the API at "/" (assets under /v2assets).
export default defineConfig({
  root: __dirname,
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5180, proxy: { "/api": "http://127.0.0.1:8000" } },
  preview: { host: "127.0.0.1", port: 5181, proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", assetsDir: "v2assets", emptyOutDir: true, chunkSizeWarningLimit: 1500 },
});

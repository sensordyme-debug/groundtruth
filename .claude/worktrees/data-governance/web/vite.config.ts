import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: proxies the engine API (api/app.py on 127.0.0.1:8000). Build: served by the API itself at "/".
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 1500 },
});

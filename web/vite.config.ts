import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The API (FastAPI orchestrator) listens on 8011. Never point this at 8000: that is another app.
// API_TARGET lets a second dev server point at something else (a throwaway mock, a staging API); the default is the real stack.
const API_TARGET = process.env.API_TARGET ?? "http://localhost:8011";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true, ws: true },
    },
  },
  preview: {
    port: 4173,
    strictPort: true,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true, ws: true },
    },
  },
  build: { outDir: "dist", sourcemap: false, target: "es2022" },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});

/// <reference types="vitest/config" />
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// Dev server: `/api`, `/healthz`, `/readyz` and `/metrics` are proxied to the Kino API.
// Default target http://127.0.0.1:8090; override with KINO_API_URL (shell env or web/.env.local),
// e.g. `KINO_API_URL=http://192.168.1.10:8080 npm run dev`.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "KINO_");
  const target = process.env.KINO_API_URL ?? env.KINO_API_URL ?? "http://127.0.0.1:8090";
  const proxy = Object.fromEntries(
    ["/api", "/healthz", "/readyz", "/metrics"].map((p) => [p, { target, changeOrigin: false }]),
  );
  return {
    base: "/",
    plugins: [react(), tailwindcss()],
    resolve: { alias: { "@": path.resolve(__dirname, "src") } },
    server: { port: 5173, proxy },
    preview: { proxy },
    build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      css: false,
    },
  };
});

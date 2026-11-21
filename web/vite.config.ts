import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// The backend (make dev) serves HTTP + WS on :8000. In dev, Vite proxies to it
// so the browser talks to one origin; set VITE_BACKEND_URL to bypass.
const backend = process.env["BACKEND_URL"] ?? "http://localhost:8000";

export default defineConfig({
    plugins: [react(), tailwindcss()],
    resolve: {
        alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    server: {
        port: 5173,
        strictPort: true,
        proxy: {
            "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
            "/health": backend,
            "/media": backend,
            "/campaign-assets": backend,
            "/dev": backend,
            "/api": backend,
            "/r": backend,
        },
    },
    build: {
        target: "es2023",
        sourcemap: true,
    },
    test: {
        environment: "jsdom",
        include: ["src/**/*.test.{ts,tsx}"],
        restoreMocks: true,
    },
});

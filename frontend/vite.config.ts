import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the FastAPI backend so the browser talks to one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.VITE_API_PROXY ?? "http://127.0.0.1:8000", changeOrigin: true } },
  },
  build: {
    chunkSizeWarningLimit: 5000,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes("plotly.js-dist-min")) return "plotly";
          if (/node_modules[\/](react|react-dom|react-router|react-router-dom|@tanstack|scheduler)[\/]/.test(id)) return "vendor";
          return undefined;
        },
      },
    },
  },
});

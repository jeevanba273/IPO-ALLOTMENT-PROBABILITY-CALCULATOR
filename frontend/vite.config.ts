import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs separately (`ipo-intel serve`); in production the
// backend serves the built files from dist/ itself.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1000,
  },
});

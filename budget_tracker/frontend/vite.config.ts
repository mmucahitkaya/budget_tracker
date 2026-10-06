import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Served under a dynamic Ingress path, so all asset URLs are relative
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  build: { outDir: "../static", emptyOutDir: true },
  server: {
    host: true,
    proxy: { "/api": "http://127.0.0.1:8099" },
  },
});

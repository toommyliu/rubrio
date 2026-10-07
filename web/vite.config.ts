import { resolve } from "node:path"
import tailwindcss from "@tailwindcss/vite"
import { tanstackRouter } from "@tanstack/router-plugin/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      "@": resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    proxy: {
      "/api":
        process.env.RUBRICATE_API_URL ||
        `http://127.0.0.1:${process.env.RUBRICATE_PORT || "8765"}`,
    },
  },
  build: {
    outDir: resolve(import.meta.dirname, "../src/rubricate/static"),
    emptyOutDir: true,
  },
})

import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: { port: 3000 },
  preview: { port: 3000 },
  build: {
    rollupOptions: {
      output: {
        // Split heavy vendors into their own cacheable chunks so the main
        // bundle stays small (kills the 500 kB chunk-size advisory).
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          if (
            id.includes("reactflow") ||
            id.includes("@reactflow") ||
            id.includes("d3-")
          )
            return "vendor-graph";
          if (id.includes("@mui") || id.includes("@emotion")) return "vendor-mui";
          if (id.includes("@tanstack")) return "vendor-query";
          if (
            id.includes("/react/") ||
            id.includes("/react-dom/") ||
            id.includes("react-router")
          )
            return "vendor-react";
          return "vendor";
        },
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    // Vitest owns unit tests under src/. Playwright owns tests/e2e/ (real
    // Chromium); scoping the include here keeps vitest from trying to run the
    // *.spec.ts Playwright files, which it cannot execute.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});

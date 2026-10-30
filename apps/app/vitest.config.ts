import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  css: { postcss: { plugins: [] } },
  resolve: {
    alias: {
      "@": resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    // Node 25+ defines its own global localStorage (undefined without a file), hiding jsdom's.
    execArgv: ["--no-experimental-webstorage"],
    server: { deps: { inline: [/@copilotkit\/react-core/] } },
  },
});

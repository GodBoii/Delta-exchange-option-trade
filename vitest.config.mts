import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL(".", import.meta.url)) } },
  test: { projects: [
    { extends: true, test: { name: "convex", environment: "edge-runtime", include: ["convex/**/*.test.ts"] } },
    { extends: true, test: { name: "library", environment: "node", include: ["tests/**/*.test.ts"] } },
  ] },
});

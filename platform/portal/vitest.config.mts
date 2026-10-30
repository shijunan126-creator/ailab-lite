import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.test.{mjs,ts,tsx}"],
    environment: "node",
    // component tests opt into the DOM per file with `// @vitest-environment jsdom`
  },
});

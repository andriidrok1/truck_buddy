import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

// The committed src/types.generated.ts must match contracts/openapi.json (refresh both when the backend changes:
// curl -s http://localhost:8001/api/openapi.json > contracts/openapi.json && node scripts/gen-types.mjs).
test("generated types match the OpenAPI snapshot", () => {
  const root = resolve(__dirname, "../..");
  expect(() => execFileSync("node", ["scripts/gen-types.mjs", "--check"], { cwd: root, stdio: "pipe" })).not.toThrow();
});

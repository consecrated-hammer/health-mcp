import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";

const bundle = await build({
  entryPoints: ["src/host-result.ts"],
  bundle: true,
  format: "esm",
  platform: "node",
  target: "node22",
  write: false,
});
const source = bundle.outputFiles[0].text;
const hostResult = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

test("normalizes ChatGPT structured toolOutput into an MCP tool result", () => {
  const output = { Entries: [], Totals: { TotalCalories: 0 } };
  assert.deepEqual(hostResult.normalizeHostToolOutput(output), { structuredContent: output });
});

test("preserves a standard MCP tool result", () => {
  const result = { structuredContent: { Entries: [] }, content: [], isError: false };
  assert.deepEqual(hostResult.normalizeHostToolOutput(result), result);
});

test("ignores an unavailable host result", () => {
  assert.equal(hostResult.normalizeHostToolOutput(undefined), null);
  assert.equal(hostResult.normalizeHostToolOutput(null), null);
});

test("distinguishes toolOutput updates from unrelated ChatGPT global changes", () => {
  assert.equal(hostResult.hasToolOutputUpdate({ theme: "dark" }), false);
  assert.equal(hostResult.hasToolOutputUpdate({ toolOutput: undefined }), true);
  assert.equal(hostResult.hasToolOutputUpdate({ toolOutput: { Entries: [] } }), true);
});

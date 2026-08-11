import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";

const bundle = await build({
  entryPoints: ["src/food-log-budget.ts"],
  bundle: true,
  format: "esm",
  platform: "node",
  target: "node22",
  write: false,
});
const source = bundle.outputFiles[0].text;
const budget = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

test("completeSubtotal refuses to understate a meal with unknown nutrition", () => {
  assert.equal(budget.completeSubtotal([300, 120.5]), 420.5);
  assert.equal(budget.completeSubtotal([300, null]), null);
});

test("targetBudget prefers Everday's authoritative remaining value", () => {
  assert.deepEqual(budget.targetBudget(1500, 1800, 600), { kind: "remaining", amount: 600 });
  assert.deepEqual(budget.targetBudget(1500, 1800, null), { kind: "remaining", amount: 300 });
  assert.deepEqual(budget.targetBudget(1900, 1800, -100), { kind: "over", amount: 100 });
  assert.deepEqual(budget.targetBudget(500, 0, 0), { kind: "none" });
});

test("proteinBudget distinguishes below, within, and above the target range", () => {
  assert.deepEqual(budget.proteinBudget(80, 100, 130, 20, 50), { kind: "remaining", amount: 20 });
  assert.deepEqual(budget.proteinBudget(115, 100, 130, -15, 15), { kind: "met", hasRange: true });
  assert.deepEqual(budget.proteinBudget(145, 100, 130, -45, -15), { kind: "over", amount: 15 });
  assert.deepEqual(budget.proteinBudget(80, null, null, null, null), { kind: "none" });
});

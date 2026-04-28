import { summarizeToolValidation } from "../src/lib/toolValidation.mjs";

const summary = summarizeToolValidation([
  { validationStatus: "passed", validationStrategy: "pytest" },
  { validationStatus: "failed", validationStrategy: "tsc" },
  { validationStatus: "skipped", validationStrategy: "pytest" },
  { validationStatus: "passed", validationStrategy: "build" },
  { validationStatus: "passed" },
]);

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

assert(summary.counts.passed === 3, `expected passed=3, got ${summary.counts.passed}`);
assert(summary.counts.failed === 1, `expected failed=1, got ${summary.counts.failed}`);
assert(summary.counts.skipped === 1, `expected skipped=1, got ${summary.counts.skipped}`);
assert(summary.strategies.length === 3, `expected 3 strategies, got ${summary.strategies.length}`);
assert(summary.strategies[0].strategy === "pytest" && summary.strategies[0].count === 2, "expected pytest to be first with count 2");
assert(summary.strategies.some((item) => item.strategy === "build" && item.count === 1), "expected build summary item");
assert(summary.strategies.some((item) => item.strategy === "tsc" && item.count === 1), "expected tsc summary item");

console.log("tool validation summary check passed");

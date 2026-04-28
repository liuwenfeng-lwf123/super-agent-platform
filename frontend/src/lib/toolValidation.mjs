export function summarizeToolValidation(toolCalls) {
  const counts = { passed: 0, failed: 0, skipped: 0 };
  const strategyCounts = new Map();

  for (const toolCall of Array.isArray(toolCalls) ? toolCalls : []) {
    const status = toolCall?.validationStatus;
    if (status === "passed" || status === "failed" || status === "skipped") {
      counts[status] += 1;
    }

    const rawStrategy = typeof toolCall?.validationStrategy === "string" ? toolCall.validationStrategy.trim() : "";
    if (!rawStrategy) continue;
    strategyCounts.set(rawStrategy, (strategyCounts.get(rawStrategy) || 0) + 1);
  }

  const strategies = Array.from(strategyCounts.entries())
    .map(([strategy, count]) => ({ strategy, count }))
    .sort((left, right) => right.count - left.count || left.strategy.localeCompare(right.strategy));

  return { counts, strategies };
}

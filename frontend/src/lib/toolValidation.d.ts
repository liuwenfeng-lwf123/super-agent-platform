export interface ToolValidationLike {
  validationStatus?: "passed" | "failed" | "skipped";
  validationStrategy?: string;
}

export interface ValidationStrategySummaryItem {
  strategy: string;
  count: number;
}

export interface ToolValidationSummary {
  counts: {
    passed: number;
    failed: number;
    skipped: number;
  };
  strategies: ValidationStrategySummaryItem[];
}

export function summarizeToolValidation(toolCalls: ToolValidationLike[]): ToolValidationSummary;

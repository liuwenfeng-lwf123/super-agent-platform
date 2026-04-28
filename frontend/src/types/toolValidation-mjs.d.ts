declare module "@/lib/toolValidation.mjs" {
  export interface ToolValidationLike {
    validationStatus?: "passed" | "failed" | "skipped";
    validationStrategy?: string;
  }
}


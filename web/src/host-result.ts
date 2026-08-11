export type ToolResult = {
  structuredContent?: unknown;
  content?: Array<{ type?: string; text?: string }>;
  isError?: boolean;
};

export function hasToolOutputUpdate(value: unknown): value is { toolOutput: unknown } {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.prototype.hasOwnProperty.call(value, "toolOutput");
}

export function normalizeHostToolOutput(value: unknown): ToolResult | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "object" && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    if ("structuredContent" in record || "content" in record || "isError" in record) {
      return record as ToolResult;
    }
  }
  return { structuredContent: value };
}

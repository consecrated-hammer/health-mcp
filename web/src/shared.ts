import { App } from "@modelcontextprotocol/ext-apps";
import { hasToolOutputUpdate, normalizeHostToolOutput, type ToolResult } from "./host-result";

export type UnknownRecord = Record<string, unknown>;
export type { ToolResult } from "./host-result";

type OpenAIHost = { toolOutput?: unknown };
type OpenAIGlobalsEvent = CustomEvent<{ globals?: { toolOutput?: unknown } }>;

function openAIHost(): OpenAIHost | undefined {
  return (window as Window & { openai?: OpenAIHost }).openai;
}

export function asRecord(value: unknown): UnknownRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as UnknownRecord)
    : {};
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function formatCalendarDate(
  value: string | null,
  locale: string,
  options: Intl.DateTimeFormatOptions,
  fallback = "Today",
): string {
  if (!value) return fallback;
  const parts = value.slice(0, 10).split("-").map(Number);
  if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) return value;
  return new Intl.DateTimeFormat(locale, options).format(new Date(parts[0], parts[1] - 1, parts[2]));
}

export function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function toolError(result: ToolResult): string {
  const text = result.content?.find((item) => item.type === "text")?.text;
  if (!text) return "The health tool could not complete this request.";
  try {
    const parsed = JSON.parse(text) as { error?: unknown };
    return asString(parsed.error) ?? text;
  } catch {
    return text;
  }
}

export function connectApp(
  name: string,
  onResult: (result: ToolResult, app: App) => void,
): void {
  const app = new App({ name, version: "1.0.0" }, {}, { autoResize: true });
  let hasRenderedResult = false;
  const deliver = (value: unknown): void => {
    const result = normalizeHostToolOutput(value);
    if (!result) return;
    hasRenderedResult = true;
    onResult(result, app);
  };

  app.addEventListener("toolresult", deliver);
  window.addEventListener(
    "openai:set_globals",
    ((event: OpenAIGlobalsEvent) => {
      const globals = event.detail?.globals;
      if (!hasToolOutputUpdate(globals)) return;
      deliver(globals.toolOutput);
    }) as EventListener,
    { passive: true },
  );
  deliver(openAIHost()?.toolOutput);
  void app.connect().catch((error: unknown) => {
    if (hasRenderedResult) {
      console.warn("MCP Apps bridge unavailable; using the ChatGPT compatibility result.", error);
      return;
    }
    const root = document.querySelector<HTMLElement>("#app");
    if (!root) return;
    root.replaceChildren(
      element("div", "error", error instanceof Error ? error.message : "Unable to connect to ChatGPT."),
    );
  });
}

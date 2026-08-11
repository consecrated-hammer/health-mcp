import "./styles.css";
import { App } from "@modelcontextprotocol/ext-apps";
import { appendHealthActions } from "./health-actions-panel";
import { asRecord, connectApp, element, toolError, ToolResult } from "./shared";

function render(result: ToolResult, app: App): void {
  const root = document.querySelector<HTMLElement>("#app");
  if (!root) return;
  if (result.isError) {
    root.replaceChildren(element("div", "error", toolError(result)));
    return;
  }
  root.replaceChildren();
  appendHealthActions(root, asRecord(result.structuredContent), app, { standalone: true });
}

connectApp("Health Actions", render);

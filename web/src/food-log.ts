import "./styles.css";
import { App } from "@modelcontextprotocol/ext-apps";
import {
  asArray,
  asNumber,
  asRecord,
  asString,
  connectApp,
  element,
  formatCalendarDate,
  toolError,
  ToolResult,
  UnknownRecord,
} from "./shared";

const locale = document.documentElement.lang || navigator.language || "en-AU";
const numberFormat = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });
const slotOrder = ["Breakfast", "Snack1", "Lunch", "Snack2", "Dinner", "Snack3"];
const slotLabels: Record<string, string> = {
  Snack1: "Morning snack",
  Snack2: "Afternoon snack",
  Snack3: "Evening snack",
};

function effectiveQuantity(entry: UnknownRecord): number {
  return asNumber(entry.DisplayQuantity) ?? asNumber(entry.Quantity) ?? 1;
}

function effectiveNutrient(entry: UnknownRecord, name: string): number | null {
  const perServing = asNumber(entry[name]);
  return perServing === null ? null : perServing * effectiveQuantity(entry);
}

function formatMetric(value: number | null, unit: string, digits = 1): string {
  if (value === null) return "—";
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(value)}${unit}`;
}

function summaryMetric(label: string, value: number | null, unit: string, target?: string): HTMLElement {
  const node = element("div", "metric food-log-metric");
  node.append(element("span", "metric-label", label));
  node.append(element("span", "metric-value", formatMetric(value, unit)));
  if (target) node.append(element("span", "metric-target", target));
  return node;
}

function mutationMessage(data: UnknownRecord): string | null {
  if (data.Created === true) return "Meal logged.";
  if (data.Updated === true) return "Meal updated.";
  if (data.Deleted === true) return "Meal removed.";
  return null;
}

function renderError(root: HTMLElement, message: string): void {
  const card = element("section", "card");
  card.append(element("h1", undefined, "Food log"));
  card.append(element("div", "error", message));
  root.replaceChildren(card);
}

function render(result: ToolResult, _app: App): void {
  const root = document.querySelector<HTMLElement>("#app");
  if (!root) return;
  if (result.isError) {
    renderError(root, toolError(result));
    return;
  }

  const data = asRecord(result.structuredContent);
  const daily = asRecord(data.DailyLog);
  const summary = asRecord(data.Summary);
  const totals = asRecord(data.Totals);
  const targets = asRecord(data.Targets);
  const entries = asArray(data.Entries).length ? asArray(data.Entries) : asArray(data.Items);
  const logDate = asString(daily.LogDate)
    ?? asString(data.LogDate)
    ?? asString(summary.LogDate)
    ?? asString(data.TargetLogDate);

  const card = element("section", "card food-log-card");
  const header = element("header", "card-header");
  const heading = element("div");
  heading.append(element("h1", undefined, "Food log"));
  heading.append(element("div", "subtitle", formatCalendarDate(logDate, locale, {
    weekday: "long",
    day: "numeric",
    month: "long",
  })));
  header.append(heading);
  card.append(header);

  const savedMessage = mutationMessage(data);
  if (savedMessage) card.append(element("div", "success food-log-status", savedMessage));
  const reason = asString(data.Reason);
  if (!savedMessage && reason) card.append(element("div", "error food-log-status", reason));

  const calorieTarget = asNumber(targets.DailyCalorieTarget);
  const proteinMin = asNumber(targets.ProteinTargetMin);
  const proteinMax = asNumber(targets.ProteinTargetMax);
  const metrics = element("div", "metrics food-log-metrics");
  metrics.append(
    summaryMetric(
      "Calories",
      asNumber(totals.TotalCalories),
      " kcal",
      calorieTarget !== null ? `of ${numberFormat.format(calorieTarget)} kcal` : undefined,
    ),
  );
  metrics.append(
    summaryMetric(
      "Protein",
      asNumber(totals.TotalProtein),
      " g",
      proteinMin !== null
        ? proteinMax !== null
          ? `${numberFormat.format(proteinMin)}–${numberFormat.format(proteinMax)} g target`
          : `${numberFormat.format(proteinMin)} g target`
        : undefined,
    ),
  );
  metrics.append(summaryMetric("Carbs", asNumber(totals.TotalCarbs), " g"));
  card.append(metrics);

  if (!entries.length) {
    card.append(element("p", "empty food-log-empty", "Nothing logged for this day."));
  } else {
    const grouped = new Map<string, UnknownRecord[]>();
    for (const item of entries) {
      const entry = asRecord(item);
      const slot = asString(entry.MealType) ?? "Other";
      grouped.set(slot, [...(grouped.get(slot) ?? []), entry]);
    }
    const slots = [...grouped.keys()].sort((left, right) => {
      const leftIndex = slotOrder.indexOf(left);
      const rightIndex = slotOrder.indexOf(right);
      return (leftIndex < 0 ? 999 : leftIndex) - (rightIndex < 0 ? 999 : rightIndex);
    });

    const table = element("div", "food-log-table");
    const tableHeader = element("div", "food-log-row food-log-table-header");
    for (const label of ["Food", "Qty", "kcal", "Protein", "Carbs"]) {
      tableHeader.append(element("span", undefined, label));
    }
    table.append(tableHeader);

    for (const slot of slots) {
      const slotHeader = element("div", "food-log-slot", slotLabels[slot] ?? slot);
      table.append(slotHeader);
      for (const entry of grouped.get(slot) ?? []) {
        const row = element("div", "food-log-row");
        const food = element("span", "food-log-food");
        food.append(element("strong", undefined, asString(entry.FoodName) ?? "Unnamed food"));
        const serving = asString(entry.ServingDescription);
        const note = asString(entry.EntryNotes);
        if (serving) food.append(element("small", undefined, serving));
        if (note) food.append(element("small", "food-log-note", note));
        row.append(food);
        row.append(element("span", "food-log-number", `${numberFormat.format(effectiveQuantity(entry))}×`));
        row.append(element("span", "food-log-number", formatMetric(effectiveNutrient(entry, "CaloriesPerServing"), "", 0)));
        row.append(element("span", "food-log-number", formatMetric(effectiveNutrient(entry, "ProteinPerServing"), " g")));
        row.append(element("span", "food-log-number", formatMetric(effectiveNutrient(entry, "CarbsPerServing"), " g")));
        table.append(row);
      }
    }
    card.append(table);
  }

  const notice = asString(data.AgentNotice);
  if (notice) card.append(element("div", "notice", notice));
  root.replaceChildren(card);
}

connectApp("Health Food Log", render);

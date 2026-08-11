import "./styles.css";
import { App } from "@modelcontextprotocol/ext-apps";
import { appendHealthActions } from "./health-actions-panel";
import { completeSubtotal, proteinBudget, targetBudget } from "./food-log-budget";
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

function sumNutrient(entries: UnknownRecord[], name: string): number | null {
  return completeSubtotal(entries.map((entry) => effectiveNutrient(entry, name)));
}

function slotTone(slot: string): string {
  if (slot === "Breakfast") return "breakfast";
  if (slot === "Lunch") return "lunch";
  if (slot === "Dinner") return "dinner";
  if (slot.startsWith("Snack")) return "snack";
  return "other";
}

function footerCell(value: string, detail?: string, tone?: string): HTMLElement {
  const node = element("span", `food-log-footer-value${tone ? ` ${tone}` : ""}`);
  node.append(element("strong", undefined, value));
  if (detail) {
    const detailNode = element("small", undefined, detail);
    detailNode.title = detail;
    node.append(detailNode);
  }
  return node;
}

function targetRemaining(total: number | null, target: number | null, authoritative: number | null): HTMLElement {
  const budget = targetBudget(total, target, authoritative);
  if (budget.kind === "none") return footerCell("—", "no target");
  return budget.kind === "remaining"
    ? footerCell(numberFormat.format(budget.amount), "remaining", "food-log-budget-left")
    : footerCell(numberFormat.format(budget.amount), "over target", "food-log-budget-over");
}

function proteinRemaining(
  total: number | null,
  minimum: number | null,
  maximum: number | null,
  remainingMinimum: number | null,
  remainingMaximum: number | null,
): HTMLElement {
  const budget = proteinBudget(total, minimum, maximum, remainingMinimum, remainingMaximum);
  if (budget.kind === "none") return footerCell("—", "no target");
  if (budget.kind === "remaining") {
    return footerCell(numberFormat.format(budget.amount), "to minimum", "food-log-budget-left");
  }
  if (budget.kind === "over") {
    return footerCell(numberFormat.format(budget.amount), "over range", "food-log-budget-over");
  }
  return footerCell("Met", budget.hasRange ? "within range" : "minimum reached", "food-log-budget-met");
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

  const savedMessage = mutationMessage(data);
  if (savedMessage) header.append(element("span", "food-log-saved", savedMessage));
  card.append(header);
  const reason = asString(data.Reason);
  if (!savedMessage && reason) card.append(element("div", "error food-log-status", reason));

  const calorieTarget = asNumber(targets.DailyCalorieTarget);
  const proteinMin = asNumber(targets.ProteinTargetMin);
  const proteinMax = asNumber(targets.ProteinTargetMax);
  const carbsTarget = asNumber(targets.CarbsTarget);
  const totalCalories = asNumber(totals.TotalCalories) ?? (entries.length ? null : 0);
  const netCalories = asNumber(totals.NetCalories);
  const totalProtein = asNumber(totals.TotalProtein) ?? (entries.length ? null : 0);
  const totalCarbs = asNumber(totals.TotalCarbs) ?? (entries.length ? null : 0);
  const remainingCalories = asNumber(totals.RemainingCalories);
  const remainingProteinMin = asNumber(totals.RemainingProteinMin);
  const remainingProteinMax = asNumber(totals.RemainingProteinMax);
  const remainingCarbs = asNumber(totals.RemainingCarbs);

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
  for (const label of ["Food", "Qty", "kcal", "Protein (g)", "Carbs (g)"]) {
    tableHeader.append(element("span", undefined, label));
  }
  table.append(tableHeader);
  if (!entries.length) table.append(element("p", "food-log-empty-row", "Nothing logged for this day."));

  for (const slot of slots) {
    const slotEntries = grouped.get(slot) ?? [];
    const slotHeader = element("div", `food-log-slot food-log-slot-${slotTone(slot)}`);
    slotHeader.append(element("strong", undefined, slotLabels[slot] ?? slot));
    slotHeader.append(
      element(
        "span",
        undefined,
        `${formatMetric(sumNutrient(slotEntries, "CaloriesPerServing"), " kcal", 0)} · ${formatMetric(sumNutrient(slotEntries, "ProteinPerServing"), " g protein")}`,
      ),
    );
    table.append(slotHeader);
    for (const entry of slotEntries) {
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
      row.append(element("span", "food-log-number", formatMetric(effectiveNutrient(entry, "ProteinPerServing"), "")));
      row.append(element("span", "food-log-number", formatMetric(effectiveNutrient(entry, "CarbsPerServing"), "")));
      table.append(row);
    }
  }

  const totalRow = element("div", "food-log-row food-log-footer-row food-log-total-row");
  totalRow.append(element("strong", "food-log-footer-label", "TOTAL"));
  totalRow.append(element("span"));
  const calorieDetail = calorieTarget !== null && calorieTarget > 0
    ? netCalories !== null && totalCalories !== null && netCalories !== totalCalories
      ? `net ${numberFormat.format(netCalories)} · target ${numberFormat.format(calorieTarget)}`
      : `target ${numberFormat.format(calorieTarget)}`
    : undefined;
  totalRow.append(footerCell(formatMetric(totalCalories, "", 0), calorieDetail));
  totalRow.append(footerCell(formatMetric(totalProtein, ""), proteinMin !== null && proteinMin > 0
    ? proteinMax !== null && proteinMax > 0
      ? `target ${numberFormat.format(proteinMin)}–${numberFormat.format(proteinMax)}`
      : `minimum ${numberFormat.format(proteinMin)}`
    : undefined));
  totalRow.append(footerCell(formatMetric(totalCarbs, ""), carbsTarget !== null && carbsTarget > 0 ? `target ${numberFormat.format(carbsTarget)}` : undefined));
  table.append(totalRow);

  const remainingRow = element("div", "food-log-row food-log-footer-row food-log-remaining-row");
  remainingRow.append(element("strong", "food-log-footer-label", "REMAINING"));
  remainingRow.append(element("span"));
  remainingRow.append(targetRemaining(totalCalories, calorieTarget, remainingCalories));
  remainingRow.append(proteinRemaining(totalProtein, proteinMin, proteinMax, remainingProteinMin, remainingProteinMax));
  remainingRow.append(targetRemaining(totalCarbs, carbsTarget, remainingCarbs));
  table.append(remainingRow);
  card.append(table);

  appendHealthActions(card, data, _app);
  root.replaceChildren(card);
}

connectApp("Health Food Log", render);

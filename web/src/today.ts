import "./styles.css";
import { App } from "@modelcontextprotocol/ext-apps";
import {
  asArray,
  asNumber,
  asRecord,
  asString,
  connectApp,
  element,
  ToolResult,
} from "./shared";

const locale = document.documentElement.lang || navigator.language || "en-AU";
const numberFormat = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 });

function formatDate(value: string | null): string {
  if (!value) return "Today";
  const parts = value.slice(0, 10).split("-").map(Number);
  if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) return value;
  return new Intl.DateTimeFormat(locale, {
    weekday: "short",
    day: "numeric",
    month: "short",
  }).format(new Date(parts[0], parts[1] - 1, parts[2]));
}

function metric(
  label: string,
  value: number,
  unit: string,
  target?: number | null,
  targetLabel?: string,
): HTMLElement {
  const node = element("div", "metric");
  node.append(element("span", "metric-label", label));
  node.append(element("span", "metric-value", `${numberFormat.format(value)}${unit}`));
  if (target !== null && target !== undefined && target > 0) {
    node.append(element("span", "metric-target", targetLabel ?? `of ${numberFormat.format(target)}${unit}`));
    const progress = element("div", "progress");
    const fill = element("span");
    fill.style.width = `${Math.min(100, Math.max(0, (value / target) * 100))}%`;
    progress.append(fill);
    node.append(progress);
  }
  return node;
}

function render(result: ToolResult, _app: App): void {
  const root = document.querySelector<HTMLElement>("#app");
  if (!root) return;
  const data = asRecord(result.structuredContent);
  const summary = asRecord(data.Summary);
  const totals = asRecord(data.Totals);
  const targets = asRecord(data.Targets);
  const daily = asRecord(data.DailyLog);

  const card = element("section", "card");
  const header = element("header", "card-header");
  const heading = element("div");
  heading.append(element("h1", undefined, "Today at a glance"));
  heading.append(element("div", "subtitle", formatDate(asString(summary.LogDate))));
  header.append(heading);
  card.append(header);

  const metrics = element("div", "metrics");
  const calories = asNumber(totals.TotalCalories) ?? asNumber(summary.TotalCalories) ?? 0;
  const calorieTarget = asNumber(targets.DailyCalorieTarget);
  metrics.append(metric("Calories", calories, " kcal", calorieTarget));

  const protein = asNumber(totals.TotalProtein) ?? asNumber(summary.TotalProtein) ?? 0;
  const proteinMin = asNumber(targets.ProteinTargetMin);
  const proteinMax = asNumber(targets.ProteinTargetMax);
  const proteinLabel = proteinMin !== null && proteinMax !== null
    ? `${numberFormat.format(proteinMin)}–${numberFormat.format(proteinMax)} g target`
    : undefined;
  metrics.append(metric("Protein", protein, " g", proteinMin, proteinLabel));

  const steps = asNumber(summary.Steps) ?? asNumber(daily.Steps) ?? 0;
  metrics.append(metric("Steps", steps, "", asNumber(targets.StepTarget)));

  const water = asNumber(daily.WaterLitres);
  if (water !== null && water > 0) metrics.append(metric("Water", water, " L"));
  const sleep = asNumber(daily.SleepHours);
  if (sleep !== null && sleep > 0) metrics.append(metric("Sleep", sleep, " h"));
  const weight = asNumber(daily.WeightKg);
  if (weight !== null && weight > 0) metrics.append(metric("Weight", weight, " kg"));
  card.append(metrics);

  const mealCount = asArray(data.Entries).length;
  const workoutCount = asArray(data.Workouts).length;
  card.append(
    element(
      "div",
      "summary-line",
      `${mealCount} ${mealCount === 1 ? "meal" : "meals"} · ${workoutCount} ${workoutCount === 1 ? "workout" : "workouts"}`,
    ),
  );
  const notice = asString(data.AgentNotice);
  if (notice) card.append(element("div", "notice", notice));
  root.replaceChildren(card);
}

connectApp("Health Today", render);

import { App } from "@modelcontextprotocol/ext-apps";
import {
  asArray,
  asNumber,
  asRecord,
  asString,
  element,
  toolError,
  ToolResult,
  UnknownRecord,
} from "./shared";

type PanelOptions = { standalone?: boolean };

function awarenessFor(data: UnknownRecord): UnknownRecord {
  return asRecord(data.TaskAwareness);
}

export function hasHealthActions(data: UnknownRecord): boolean {
  const awareness = awarenessFor(data);
  return asRecord(awareness.ActionForm).Required === true;
}

function actionSection(title: string, description?: string): HTMLElement {
  const section = element("section", "health-action");
  section.append(element("h3", undefined, title));
  if (description) section.append(element("p", "hint", description));
  return section;
}

function field(label: string, control: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement): HTMLElement {
  const wrapper = element("label", "health-action-field");
  wrapper.append(element("span", undefined, label), control);
  return wrapper;
}

function button(label: string, className = "primary"): HTMLButtonElement {
  const node = element("button", className, label);
  node.type = "button";
  return node;
}

function statusNode(): HTMLElement {
  const status = element("div", "health-action-status");
  status.hidden = true;
  return status;
}

async function callTool(app: App, name: string, arguments_: UnknownRecord): Promise<UnknownRecord> {
  const result = await app.callServerTool({ name, arguments: arguments_ }) as ToolResult;
  if (result.isError) throw new Error(toolError(result));
  return asRecord(result.structuredContent);
}

function report(status: HTMLElement, message: string, error = false): void {
  status.hidden = false;
  status.className = error ? "error health-action-status" : "success health-action-status";
  status.textContent = message;
}

function tellModel(app: App, message: string): void {
  void app.updateModelContext({ content: [{ type: "text", text: message }] }).catch(() => undefined);
}

function scoreSelect(): HTMLSelectElement {
  const select = element("select");
  const placeholder = element("option", undefined, "Choose…");
  placeholder.value = "";
  select.append(placeholder);
  for (let value = 1; value <= 10; value += 1) {
    const option = element("option", undefined, String(value));
    option.value = String(value);
    select.append(option);
  }
  return select;
}

function appendDinnerReflection(panel: HTMLElement, awareness: UnknownRecord, app: App): void {
  const reminder = asRecord(awareness.DinnerReflectionReminder);
  if (reminder.NeedsLogging !== true) return;
  const date = asString(reminder.LogDate);
  const missing = new Set(asArray(reminder.MissingFields).map(asString).filter(Boolean));
  if (!date || !missing.size) return;
  const section = actionSection(`Finish the dinner reflection for ${date}`);
  const hunger = missing.has("HungerBeforeDinner") ? scoreSelect() : null;
  const satisfaction = missing.has("OverallSatisfaction") ? scoreSelect() : null;
  if (hunger) section.append(field("Hunger before dinner (1–10)", hunger));
  if (satisfaction) section.append(field("Overall satisfaction (1–10)", satisfaction));
  const save = button("Save reflection");
  const status = statusNode();
  section.append(save, status);
  save.addEventListener("click", async () => {
    const arguments_: UnknownRecord = { date };
    if (hunger?.value) arguments_.hunger_before_dinner = Number(hunger.value);
    if (satisfaction?.value) arguments_.overall_satisfaction = Number(satisfaction.value);
    if (Object.keys(arguments_).length === 1) {
      report(status, "Choose the missing scores before saving.", true);
      return;
    }
    save.disabled = true;
    try {
      await callTool(app, "update_daily_log", arguments_);
      report(status, "Dinner reflection saved.");
      if (hunger) hunger.disabled = true;
      if (satisfaction) satisfaction.disabled = true;
      save.hidden = true;
      tellModel(app, `The user completed the dinner reflection for ${date} from the Health Actions App.`);
    } catch (caught: unknown) {
      report(status, caught instanceof Error ? caught.message : "The reflection could not be saved.", true);
      save.disabled = false;
    }
  });
  panel.append(section);
}

function appendDailyDetails(panel: HTMLElement, awareness: UnknownRecord, data: UnknownRecord, app: App): void {
  const reminder = asRecord(awareness.DailyDetailsReminder);
  if (reminder.NeedsLogging !== true) return;
  const office = asRecord(reminder.OfficeMode);
  const period = asRecord(reminder.Period);
  const date = asString(office.LogDate) ?? asString(period.LogDate) ?? asString(data.LinkedToday);
  if (!date) return;
  const section = actionSection(`Complete daily details for ${date}`);
  let officeSelect: HTMLSelectElement | null = null;
  let periodSelect: HTMLSelectElement | null = null;
  if (office.NeedsLogging === true) {
    officeSelect = element("select");
    for (const [value, label] of [["", "Choose…"], ["office", "Office"], ["wfh", "WFH"], ["other", "Other"]]) {
      const option = element("option", undefined, label);
      option.value = value;
      officeSelect.append(option);
    }
    section.append(field("Work location", officeSelect));
  }
  if (period.NeedsLogging === true) {
    periodSelect = element("select");
    for (const label of ["Choose…", "No", "Day 1", "Day 2", "Day 3", "Day 4", "Day 5", "Day 6", "Day 7"]) {
      const option = element("option", undefined, label);
      option.value = label === "Choose…" ? "" : label;
      periodSelect.append(option);
    }
    section.append(field("Period status", periodSelect));
  }
  const save = button("Save daily details");
  const status = statusNode();
  section.append(save, status);
  save.addEventListener("click", async () => {
    const arguments_: UnknownRecord = { date };
    if (officeSelect?.value) arguments_.office_mode = officeSelect.value;
    if (periodSelect?.value) {
      arguments_.period_label = periodSelect.value;
      arguments_.period = periodSelect.value !== "No";
    }
    if (Object.keys(arguments_).length === 1) {
      report(status, "Choose the missing details before saving.", true);
      return;
    }
    save.disabled = true;
    try {
      await callTool(app, "update_daily_log", arguments_);
      report(status, "Daily details saved.");
      if (officeSelect) officeSelect.disabled = true;
      if (periodSelect) periodSelect.disabled = true;
      save.hidden = true;
      tellModel(app, `The user completed daily details for ${date} from the Health Actions App.`);
    } catch (caught: unknown) {
      report(status, caught instanceof Error ? caught.message : "The details could not be saved.", true);
      save.disabled = false;
    }
  });
  panel.append(section);
}

function appendWeight(panel: HTMLElement, awareness: UnknownRecord, data: UnknownRecord, app: App): void {
  const reminder = asRecord(awareness.WeightReminder);
  if (reminder.NeedsLogging !== true) return;
  const date = asString(reminder.LogDate) ?? asString(data.LinkedToday);
  if (!date) return;
  const section = actionSection("Log a current weight", asString(reminder.LastLoggedDate)
    ? `Last logged ${asString(reminder.LastLoggedDate)}.`
    : "No recent weight is recorded.");
  const weight = element("input");
  weight.type = "number";
  weight.min = "20";
  weight.max = "500";
  weight.step = "0.1";
  const save = button("Save weight");
  const status = statusNode();
  section.append(field("Weight (kg)", weight), save, status);
  save.addEventListener("click", async () => {
    const value = Number(weight.value);
    if (!Number.isFinite(value) || value < 20 || value > 500) {
      report(status, "Enter a weight between 20 and 500 kg.", true);
      return;
    }
    save.disabled = true;
    try {
      await callTool(app, "log_weight", { date, weight_kg: value });
      report(status, "Weight saved.");
      weight.disabled = true;
      save.hidden = true;
      tellModel(app, `The user logged weight for ${date} from the Health Actions App.`);
    } catch (caught: unknown) {
      report(status, caught instanceof Error ? caught.message : "The weight could not be saved.", true);
      save.disabled = false;
    }
  });
  panel.append(section);
}

function formatNumber(value: unknown, suffix = ""): string | null {
  const number = asNumber(value);
  return number === null ? null : `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(number)}${suffix}`;
}

function appendWeeklyReview(panel: HTMLElement, awareness: UnknownRecord, app: App): void {
  const reminder = asRecord(awareness.WeeklyReview);
  const weekStart = asString(reminder.WeekStart);
  if (reminder.Due !== true || !weekStart) return;
  const section = actionSection(`Weekly review · ${weekStart}`, "Review the derived week, then capture two short reflections.");
  const metrics = element("div", "health-action-summary", "Loading weekly summary…");
  const win = element("textarea");
  const improvement = element("textarea");
  const save = button("Save weekly review");
  const status = statusNode();
  section.append(metrics, field("Biggest nutrition win", win), field("Improvement for next week", improvement), save, status);
  panel.append(section);

  void callTool(app, "get_weekly_review", { week_start: weekStart }).then((result) => {
    const item = asRecord(result.Item);
    const note = asRecord(item.Note);
    win.value = asString(note.BiggestNutritionWin) ?? "";
    improvement.value = asString(note.ImprovementForNextWeek) ?? "";
    const values = [
      ["Avg calories", formatNumber(item.AverageCalories, " kcal")],
      ["Avg protein", formatNumber(item.AverageProtein, " g")],
      ["Avg steps", formatNumber(item.AverageSteps)],
      ["Weight change", formatNumber(item.WeightChange, " kg")],
    ].filter((row): row is string[] => Boolean(row[1]));
    metrics.replaceChildren(...values.map(([label, value]) => element("span", undefined, `${label}: ${value}`)));
    if (!values.length) metrics.textContent = "No derived weekly measurements are available yet.";
  }).catch(() => {
    metrics.textContent = "The weekly summary could not be loaded; reflections can still be saved.";
  });

  save.addEventListener("click", async () => {
    const winValue = win.value.trim();
    const improvementValue = improvement.value.trim();
    if (!winValue && !improvementValue) {
      report(status, "Add at least one reflection before saving.", true);
      return;
    }
    save.disabled = true;
    try {
      const arguments_: UnknownRecord = { week_start: weekStart };
      if (winValue) arguments_.biggest_nutrition_win = winValue;
      if (improvementValue) arguments_.improvement_for_next_week = improvementValue;
      await callTool(app, "upsert_weekly_review_note", arguments_);
      report(status, "Weekly review saved.");
      win.disabled = true;
      improvement.disabled = true;
      save.hidden = true;
      tellModel(app, `The user saved the weekly review for ${weekStart} from the Health Actions App.`);
    } catch (caught: unknown) {
      report(status, caught instanceof Error ? caught.message : "The weekly review could not be saved.", true);
      save.disabled = false;
    }
  });
}

function appendTasks(panel: HTMLElement, awareness: UnknownRecord, app: App): void {
  const tasks = [...asArray(awareness.Overdue), ...asArray(awareness.Upcoming)].map(asRecord);
  if (!tasks.length) return;
  const section = actionSection("Health tasks");
  for (const task of tasks) {
    const id = asNumber(task.Id);
    if (id === null) continue;
    const row = element("div", "health-task-row");
    const details = element("div");
    details.append(element("strong", undefined, asString(task.Title) ?? "Health task"));
    const due = asString(task.DueTime);
    if (due) details.append(element("div", "hint", `Due ${due}`));
    const actions = element("div", "health-task-actions");
    const complete = button("Complete", "secondary");
    const snooze = button("Snooze 1h", "secondary");
    const status = statusNode();
    actions.append(complete, snooze);
    row.append(details, actions, status);
    complete.addEventListener("click", async () => {
      complete.disabled = true;
      snooze.disabled = true;
      try {
        await callTool(app, "complete_health_task", { task_id: id });
        report(status, "Task completed.");
        actions.hidden = true;
        tellModel(app, `The user completed Health task ${id} from the Health Actions App.`);
      } catch (caught: unknown) {
        report(status, caught instanceof Error ? caught.message : "The task could not be completed.", true);
        complete.disabled = false;
        snooze.disabled = false;
      }
    });
    snooze.addEventListener("click", async () => {
      complete.disabled = true;
      snooze.disabled = true;
      try {
        await callTool(app, "snooze_health_task", { task_id: id, minutes: 60 });
        report(status, "Task snoozed for one hour.");
        actions.hidden = true;
        tellModel(app, `The user snoozed Health task ${id} for one hour from the Health Actions App.`);
      } catch (caught: unknown) {
        report(status, caught instanceof Error ? caught.message : "The task could not be snoozed.", true);
        complete.disabled = false;
        snooze.disabled = false;
      }
    });
    section.append(row);
  }
  panel.append(section);
}

export function appendHealthActions(
  parent: HTMLElement,
  data: UnknownRecord,
  app: App,
  options: PanelOptions = {},
): boolean {
  if (!hasHealthActions(data)) {
    if (options.standalone) {
      const card = element("section", "card");
      card.append(element("h1", undefined, "Health actions"));
      card.append(element("p", "success", "Nothing needs your attention right now."));
      parent.append(card);
    }
    return false;
  }

  const awareness = awarenessFor(data);
  const panel = element("section", options.standalone ? "card health-actions" : "health-actions");
  panel.append(element(options.standalone ? "h1" : "h2", undefined, "Things to finish"));
  appendDinnerReflection(panel, awareness, app);
  appendDailyDetails(panel, awareness, data, app);
  appendWeight(panel, awareness, data, app);
  appendWeeklyReview(panel, awareness, app);
  appendTasks(panel, awareness, app);
  parent.append(panel);
  return true;
}

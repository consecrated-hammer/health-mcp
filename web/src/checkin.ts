import "./styles.css";
import { App } from "@modelcontextprotocol/ext-apps";
import {
  asNumber,
  asRecord,
  asString,
  connectApp,
  element,
  toolError,
  ToolResult,
  UnknownRecord,
} from "./shared";

type Input = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;

function field(label: string, control: Input, hint?: string, full = false): HTMLElement {
  const wrapper = element("div", `field${full ? " full" : ""}`);
  const labelNode = element("label", undefined, label);
  labelNode.htmlFor = control.id;
  wrapper.append(labelNode, control);
  if (hint) wrapper.append(element("div", "hint", hint));
  return wrapper;
}

function input(id: string, type = "text", value: string | null = null): HTMLInputElement {
  const node = element("input");
  node.id = id;
  node.type = type;
  node.value = value ?? "";
  return node;
}

function textarea(id: string, value: string | null): HTMLTextAreaElement {
  const node = element("textarea");
  node.id = id;
  node.value = value ?? "";
  return node;
}

function localDateTime(value: string | null, timeZone: string): string {
  if (!value) return "";
  if (!/([zZ]|[+-]\d{2}:\d{2})$/.test(value)) return value.slice(0, 16);
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(parsed);
    const part = (name: string): string => parts.find((item) => item.type === name)?.value ?? "";
    return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
  } catch {
    return value.slice(0, 16);
  }
}

function linkedDateTime(value: string): string | undefined {
  return value ? `${value}:00` : undefined;
}

function optional(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed || undefined;
}

function renderSaved(root: HTMLElement, data: UnknownRecord): void {
  const headache = asRecord(data.Headache);
  const linkedMedication = asRecord(data.MedicationDose);
  const standaloneMedication = headache.HeadacheEventId ? {} : data;
  const medication = Object.keys(linkedMedication).length ? linkedMedication : standaloneMedication;
  const hasHeadache = Boolean(asString(headache.HeadacheEventId));
  const hasMedication = Boolean(asString(medication.MedicationDoseId));
  const linkageMatches = !hasHeadache || !hasMedication
    || asString(medication.HeadacheEventId) === asString(headache.HeadacheEventId);

  const card = element("section", "card");
  card.append(element("h1", undefined, "Health check-in saved"));
  card.append(
    element(
      "div",
      linkageMatches ? "success" : "error",
      hasHeadache && hasMedication
        ? linkageMatches
          ? "The headache and medication dose were saved and linked."
          : "Both records were saved, but their linkage does not match."
        : hasHeadache
          ? "The headache was saved."
          : "The medication dose was saved.",
    ),
  );

  const details = element("dl", "saved-grid");
  const rows: Array<[string, string | null]> = hasHeadache
    ? [
        ["Date", asString(headache.LogDate)],
        ["Severity", asNumber(headache.Severity)?.toString() ?? "Not recorded"],
        ["Location", asString(headache.Location)],
        ["Medication", asString(medication.MedicationName)],
        ["Dose", asString(medication.Dose)],
      ]
    : [
        ["Date", asString(medication.LogDate)],
        ["Medication", asString(medication.MedicationName)],
        ["Dose", asString(medication.Dose)],
      ];
  for (const [label, value] of rows) {
    if (!value) continue;
    details.append(element("dt", undefined, label), element("dd", undefined, value));
  }
  card.append(details);

  const ids = element("details");
  ids.append(element("summary", undefined, "Record IDs"));
  if (hasHeadache) ids.append(element("div", undefined, `Headache: ${asString(headache.HeadacheEventId)}`));
  if (hasMedication) ids.append(element("div", undefined, `Medication: ${asString(medication.MedicationDoseId)}`));
  card.append(ids);
  root.replaceChildren(card);
}

function renderDraft(root: HTMLElement, data: UnknownRecord, app: App): void {
  const draft = asRecord(data.Draft);
  const timeZone = asString(draft.timezone) ?? "UTC";
  let severity = asNumber(draft.severity);

  const card = element("section", "card");
  const header = element("header", "card-header");
  const heading = element("div");
  heading.append(element("h1", undefined, "Review health check-in"));
  heading.append(
    element("div", "subtitle", `Nothing is saved until you press Save. Times use ${timeZone}.`),
  );
  header.append(heading);
  card.append(header);

  const grid = element("div", "field-grid");
  const recordType = element("select");
  recordType.id = "record-type";
  for (const [value, label] of [["headache", "Headache + optional medication"], ["medication", "Medication only"]]) {
    const option = element("option", undefined, label);
    option.value = value;
    option.selected = asString(draft.record_type) === value;
    recordType.append(option);
  }
  grid.append(field("What are you recording?", recordType, undefined, true));

  const date = input("event-date", "date", asString(draft.date));
  date.required = true;
  grid.append(field("Date", date));

  const onset = input("onset-at", "datetime-local", localDateTime(asString(draft.onset_at), timeZone));
  const onsetField = field("Headache onset", onset, "Leave blank when the exact time is unknown.");
  grid.append(onsetField);

  const severityField = element("div", "field full");
  severityField.append(element("span", "field-label", "Severity (optional)"));
  const severityButtons = element("div", "severity");
  const severityHint = element("div", "hint");
  const scaleText = (value: number | null): string => {
    if (value === null) return "Not recorded — choose only if this reflects what you felt.";
    if (value <= 3) return "1–3 mild: noticeable; normal activity mostly possible.";
    if (value <= 6) return "4–6 moderate: interferes with some activity.";
    if (value <= 9) return "7–9 severe: normal activity difficult.";
    return "10: worst imaginable.";
  };
  const updateSeverity = (): void => {
    for (const button of severityButtons.querySelectorAll<HTMLButtonElement>("button")) {
      const selected = Number(button.dataset.value) === severity;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    }
    severityHint.textContent = scaleText(severity);
  };
  for (let value = 1; value <= 10; value += 1) {
    const button = element("button", undefined, String(value));
    button.type = "button";
    button.dataset.value = String(value);
    button.addEventListener("click", () => { severity = value; updateSeverity(); });
    severityButtons.append(button);
  }
  const clearSeverity = element("button", "severity-clear", "Clear severity");
  clearSeverity.type = "button";
  clearSeverity.addEventListener("click", () => { severity = null; updateSeverity(); });
  severityField.append(severityButtons, clearSeverity, severityHint);
  grid.append(severityField);
  updateSeverity();

  const location = input("location", "text", asString(draft.location));
  const notes = textarea("notes", asString(draft.notes));
  const headacheFields = [
    field("Location", location, "For example: behind the eyes or left temple."),
    field("Context or time window", notes, "Keep approximate timing here instead of inventing an exact onset.", true),
  ];
  for (const node of headacheFields) grid.append(node);

  const medicationEnabled = input("medication-enabled", "checkbox");
  medicationEnabled.checked = asString(draft.record_type) === "medication"
    || Boolean(asString(draft.medication_name) || asString(draft.medication_dose) || asString(draft.medication_taken_at));
  const medicationToggle = element("label", "checkbox");
  medicationToggle.append(medicationEnabled, document.createTextNode("Medication was taken"));
  const medicationToggleField = element("div", "field full");
  medicationToggleField.append(medicationToggle);
  grid.append(medicationToggleField);

  const medicationName = input("medication-name", "text", asString(draft.medication_name));
  const medicationDose = input("medication-dose", "text", asString(draft.medication_dose));
  const medicationTaken = input(
    "medication-taken-at",
    "datetime-local",
    localDateTime(asString(draft.medication_taken_at), timeZone),
  );
  const medicationNotes = textarea("medication-notes", asString(draft.medication_notes));
  const medicationFields = [
    field("Medication", medicationName, "Required when recording a dose."),
    field("Dose", medicationDose, "Use only the stated amount, such as 2 tablets."),
    field("Time taken", medicationTaken, "Leave blank when the exact time is unknown."),
    field("Medication notes", medicationNotes),
  ];
  for (const node of medicationFields) grid.append(node);
  card.append(grid);

  const error = element("div", "error");
  error.hidden = true;
  card.append(error);
  const actions = element("div", "actions");
  const save = element("button", "primary", "Save check-in");
  save.type = "button";
  actions.append(save);
  card.append(actions);

  const updateMode = (): void => {
    const isHeadache = recordType.value === "headache";
    onsetField.hidden = !isHeadache;
    severityField.hidden = !isHeadache;
    for (const node of headacheFields) node.hidden = !isHeadache;
    medicationEnabled.checked = isHeadache ? medicationEnabled.checked : true;
    medicationToggleField.hidden = !isHeadache;
    for (const node of medicationFields) node.hidden = isHeadache && !medicationEnabled.checked;
  };
  recordType.addEventListener("change", updateMode);
  medicationEnabled.addEventListener("change", updateMode);
  updateMode();

  save.addEventListener("click", async () => {
    error.hidden = true;
    if (!date.value) {
      error.textContent = "Choose the date for this check-in.";
      error.hidden = false;
      return;
    }
    const isHeadache = recordType.value === "headache";
    const includeMedication = !isHeadache || medicationEnabled.checked;
    if (includeMedication && !medicationName.value.trim()) {
      error.textContent = "Enter the medication name, or turn off the medication section.";
      error.hidden = false;
      return;
    }

    const idempotencyKey = asString(draft.idempotency_key);
    if (!idempotencyKey) {
      error.textContent = "This draft is missing its retry key. Please ask ChatGPT to prepare it again.";
      error.hidden = false;
      return;
    }
    const arguments_: UnknownRecord = isHeadache
      ? {
          idempotency_key: idempotencyKey,
          date: date.value,
          onset_at: linkedDateTime(onset.value),
          severity: severity ?? undefined,
          location: optional(location.value),
          notes: optional(notes.value),
          medication_name: includeMedication ? optional(medicationName.value) : undefined,
          medication_dose: includeMedication ? optional(medicationDose.value) : undefined,
          medication_taken_at: includeMedication ? linkedDateTime(medicationTaken.value) : undefined,
          medication_notes: includeMedication ? optional(medicationNotes.value) : undefined,
        }
      : {
          idempotency_key: idempotencyKey,
          medication_name: optional(medicationName.value),
          dose: optional(medicationDose.value),
          date: date.value,
          taken_at: linkedDateTime(medicationTaken.value),
          notes: optional(medicationNotes.value),
        };
    for (const key of Object.keys(arguments_)) {
      if (arguments_[key] === undefined) delete arguments_[key];
    }

    save.disabled = true;
    save.textContent = "Saving…";
    try {
      const result = await app.callServerTool({
        name: isHeadache ? "log_headache" : "log_medication_dose",
        arguments: arguments_,
      }) as ToolResult;
      if (result.isError) throw new Error(toolError(result));
      const saved = asRecord(result.structuredContent);
      renderSaved(root, saved);
      const savedHeadache = asRecord(saved.Headache);
      const savedMedication = isHeadache ? asRecord(saved.MedicationDose) : saved;
      const summary = isHeadache
        ? [
            "Health check-in saved from the app.",
            `Headache ID: ${asString(savedHeadache.HeadacheEventId) ?? "unknown"}.`,
            `Date: ${asString(savedHeadache.LogDate) ?? date.value}.`,
            `Severity: ${asNumber(savedHeadache.Severity)?.toString() ?? "not recorded"}.`,
            asString(savedMedication.MedicationDoseId)
              ? `Medication ID: ${asString(savedMedication.MedicationDoseId)}; linked headache ID: ${asString(savedMedication.HeadacheEventId) ?? "missing"}.`
              : "No medication dose was saved.",
          ].join(" ")
        : [
            "Standalone medication dose saved from the Health check-in app.",
            `Medication ID: ${asString(savedMedication.MedicationDoseId) ?? "unknown"}.`,
            `Date: ${asString(savedMedication.LogDate) ?? date.value}.`,
          ].join(" ");
      void app.updateModelContext({ content: [{ type: "text", text: summary }] }).catch(() => undefined);
    } catch (caught: unknown) {
      error.textContent = caught instanceof Error ? caught.message : "The check-in could not be saved.";
      error.hidden = false;
      save.disabled = false;
      save.textContent = "Save check-in";
    }
  });

  root.replaceChildren(card);
}

function render(result: ToolResult, app: App): void {
  const root = document.querySelector<HTMLElement>("#app");
  if (!root) return;
  if (result.isError) {
    root.replaceChildren(element("div", "error", toolError(result)));
    return;
  }
  const data = asRecord(result.structuredContent);
  if (data.Draft) renderDraft(root, data, app);
  else renderSaved(root, data);
}

connectApp("Health Check-in", render);

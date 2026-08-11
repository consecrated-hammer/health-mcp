export type TargetBudget =
  | { kind: "none" }
  | { kind: "remaining"; amount: number }
  | { kind: "over"; amount: number };

export type ProteinBudget =
  | { kind: "none" }
  | { kind: "remaining"; amount: number }
  | { kind: "over"; amount: number }
  | { kind: "met"; hasRange: boolean };

export function completeSubtotal(values: Array<number | null>): number | null {
  if (values.some((value) => value === null)) return null;
  return values.reduce<number>((total, value) => total + (value ?? 0), 0);
}

export function targetBudget(
  total: number | null,
  target: number | null,
  authoritativeRemaining: number | null,
): TargetBudget {
  if (total === null || target === null || target <= 0) return { kind: "none" };
  const difference = authoritativeRemaining ?? (target - total);
  return difference >= 0
    ? { kind: "remaining", amount: difference }
    : { kind: "over", amount: Math.abs(difference) };
}

export function proteinBudget(
  total: number | null,
  minimum: number | null,
  maximum: number | null,
  remainingMinimum: number | null,
  remainingMaximum: number | null,
): ProteinBudget {
  if (total === null || minimum === null || minimum <= 0) return { kind: "none" };
  if (total < minimum) {
    return { kind: "remaining", amount: remainingMinimum ?? (minimum - total) };
  }
  if (maximum !== null && maximum > 0 && total > maximum) {
    return { kind: "over", amount: Math.abs(remainingMaximum ?? (maximum - total)) };
  }
  return { kind: "met", hasRange: maximum !== null && maximum > 0 };
}

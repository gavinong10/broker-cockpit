// Pure helpers for the baskets UI (cards, detail page). Kept out of
// components so they get vitest coverage.

/** Calendar days from `now`'s local date to an ISO date (YYYY-MM-DD).
 * 0 = today, negative = past. Time of day never shifts the count. */
export function daysUntil(isoDate: string, now: Date = new Date()): number {
  const [y, m, d] = isoDate.split("-").map(Number);
  const target = Date.UTC(y, m - 1, d);
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((target - today) / 86_400_000);
}

export type ExpiryTone = "red" | "amber" | "neutral";

/** Runway urgency for the nearest-expiry chip: red < 10d, amber < 30d. */
export function expiryTone(days: number): ExpiryTone {
  if (days < 10) return "red";
  if (days < 30) return "amber";
  return "neutral";
}

/** Cut to at most `max` chars, ending in a single ellipsis character,
 * with no trailing whitespace before it. Short strings pass through. */
export function truncate(text: string, max = 140): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1).trimEnd()}…`;
}

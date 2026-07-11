export type AccessEvent = { actor: string; category: string; at: string };

export type UserAccess = {
  lastLoginAt: string | null;
  count30d: number;
  recent: AccessEvent[]; // newest first, capped
};

const RECENT_CAP = 10;
const THIRTY_DAYS_MS = 30 * 24 * 3600 * 1000;

/** Group audit access events per allowlisted user. Events from actors not in
 * `userEmails` (e.g. rejected sign-ins by strangers) land in `other`. */
export function groupAccessHistory(
  events: AccessEvent[],
  userEmails: string[],
  now: Date,
): { byUser: Map<string, UserAccess>; other: AccessEvent[] } {
  const allowed = new Set(userEmails);
  const byUser = new Map<string, UserAccess>();
  for (const email of userEmails) {
    byUser.set(email, { lastLoginAt: null, count30d: 0, recent: [] });
  }
  const other: AccessEvent[] = [];
  const sorted = [...events].sort((a, b) => (a.at < b.at ? 1 : -1));
  for (const e of sorted) {
    if (!allowed.has(e.actor)) {
      other.push(e);
      continue;
    }
    const u = byUser.get(e.actor)!;
    if (e.category === "auth.login") {
      if (u.lastLoginAt === null) u.lastLoginAt = e.at;
      if (now.getTime() - Date.parse(e.at) <= THIRTY_DAYS_MS) u.count30d += 1;
    }
    if (u.recent.length < RECENT_CAP) u.recent.push(e);
  }
  return { byUser, other };
}

const CHICAGO_FMT = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Chicago",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

export const formatChicago = (iso: string) => CHICAGO_FMT.format(new Date(iso));

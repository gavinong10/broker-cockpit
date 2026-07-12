import type { Role } from "./roles";

export type NavRoute =
  | "/"
  | "/capabilities"
  | "/exposure"
  | "/admin"
  | "/journal"
  | "/features";

export type NavTab = { href: NavRoute; label: string };

/** The primary-nav tab list for an EFFECTIVE role. Every signed-in role sees
 * the read-only pages; only Users stays owner-only (coordinator-confirmed
 * owner decision 2026-07-11) — it lists guests' emails and sign-in history,
 * which viewers must not see leaked to each other. Hiding that link is UX (the
 * /admin page re-checks the role server-side). This is the single source of
 * truth consumed by BOTH the desktop bar and the mobile drawer, so the gate is
 * identical in both. */
export function navTabsFor(role: Role): NavTab[] {
  const tabs: NavTab[] = [
    { href: "/", label: "Portfolio" },
    { href: "/exposure", label: "Exposure" },
    { href: "/journal", label: "Journal" },
    { href: "/capabilities", label: "Capabilities" },
    { href: "/features", label: "Features" },
  ];
  // Owner-only per direct user decision 2026-07-11: Admin tab leaks guests'
  // emails/sign-in history; do NOT ungate.
  if (role === "owner") {
    tabs.push({ href: "/admin", label: "Users" });
  }
  return tabs;
}

import Link from "next/link";
import { getViewerContext } from "@/lib/viewerContext";

export type NavRoute = "/" | "/capabilities" | "/exposure" | "/admin" | "/journal" | "/features";

/** Minimal text-link nav for the dashboard header. Every signed-in role sees
 * the read-only pages; only Users stays owner-only (coordinator-confirmed
 * owner decision 2026-07-11) — it lists guests' emails and sign-in history,
 * which viewers must not see leaked to each other. Hiding that link is UX
 * (the /admin page re-checks the role server-side). The role comes from the
 * EFFECTIVE view, so an owner in viewer-preview sees exactly the viewer tab
 * set. */
export default async function NavTabs({ active }: { active: NavRoute }) {
  const { role } = await getViewerContext();

  const tabs: { href: NavRoute; label: string }[] = [
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

  return (
    <nav aria-label="Primary" className="flex items-center gap-5 text-sm">
      {tabs.map((t) =>
        t.href === active ? (
          <Link
            key={t.href}
            href={t.href}
            aria-current="page"
            className="font-medium text-ink"
          >
            {t.label}
          </Link>
        ) : (
          <Link
            key={t.href}
            href={t.href}
            className="text-ink-2 transition-colors hover:text-ink"
          >
            {t.label}
          </Link>
        ),
      )}
    </nav>
  );
}

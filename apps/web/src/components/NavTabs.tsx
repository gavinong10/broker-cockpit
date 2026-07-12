import Link from "next/link";
import MobileNav from "@/components/MobileNav";
import { navTabsFor, type NavRoute } from "@/lib/nav";
import { getViewerContext } from "@/lib/viewerContext";

export type { NavRoute };

/** Minimal text-link nav for the dashboard header. The role comes from the
 * EFFECTIVE view, so an owner in viewer-preview sees exactly the viewer tab
 * set. The tab list (incl. the owner-only Users gate) lives in navTabsFor so
 * the same gated array feeds both the desktop bar and the mobile drawer. */
export default async function NavTabs({
  active,
  signOut,
}: {
  active: NavRoute;
  /** Optional sign-out server action, surfaced inside the mobile drawer. */
  signOut?: () => Promise<void>;
}) {
  const { role, email } = await getViewerContext();

  const tabs = navTabsFor(role);

  return (
    <>
      {/* Desktop: horizontal bar (sm and up). */}
      <nav
        aria-label="Primary"
        className="hidden items-center gap-5 text-sm sm:flex"
      >
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
      {/* Mobile: hamburger + dropdown of the SAME (already role-gated) tabs. */}
      <MobileNav
        className="ml-auto sm:hidden"
        tabs={tabs}
        active={active}
        email={email}
        role={role}
        signOut={signOut}
      />
    </>
  );
}

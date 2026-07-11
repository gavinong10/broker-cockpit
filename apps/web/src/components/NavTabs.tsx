import Link from "next/link";
import type { Role } from "@/lib/roles";

/** Minimal text-link nav for the dashboard header. The Capabilities tab is
 * owner-only cosmetically here; the /capabilities page re-checks the role
 * server-side, so hiding the link is UX, not security. */
export default function NavTabs({
  role,
  active,
}: {
  role: Role;
  active: "/" | "/capabilities";
}) {
  const tabs: { href: "/" | "/capabilities"; label: string }[] = [
    { href: "/", label: "Portfolio" },
  ];
  if (role === "owner") tabs.push({ href: "/capabilities", label: "Capabilities" });

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

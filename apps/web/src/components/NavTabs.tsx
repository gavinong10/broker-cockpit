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
    <nav aria-label="Primary" className="flex items-center gap-4 text-sm">
      {tabs.map((t) =>
        t.href === active ? (
          <Link
            key={t.href}
            href={t.href}
            aria-current="page"
            className="border-b-2 border-zinc-950 pb-0.5 font-medium text-zinc-950 dark:border-zinc-50 dark:text-zinc-50"
          >
            {t.label}
          </Link>
        ) : (
          <Link
            key={t.href}
            href={t.href}
            className="border-b-2 border-transparent pb-0.5 text-zinc-500 hover:text-zinc-950 dark:text-zinc-400 dark:hover:text-zinc-50"
          >
            {t.label}
          </Link>
        ),
      )}
    </nav>
  );
}

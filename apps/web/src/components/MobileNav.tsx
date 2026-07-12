"use client";

// Presentational-only mobile navigation. Renders the hamburger button and a
// dropdown panel of the SAME tab list the desktop bar shows. IMPORTANT: this
// component does NO role resolution — the already-gated `tabs` array (Users
// appended for owners only) is computed by the server NavTabs and passed down,
// so the owner-only Users gate survives here unchanged. Below `sm` only.

import Link from "next/link";
import { useEffect, useState } from "react";
import type { NavRoute } from "@/lib/nav";

type Tab = { href: NavRoute; label: string };

export default function MobileNav({
  tabs,
  active,
  email,
  role,
  signOut,
  className,
}: {
  tabs: Tab[];
  active: NavRoute;
  email: string;
  role: string | null;
  /** Optional server action; rendered as a sign-out form inside the drawer. */
  signOut?: () => Promise<void>;
  className?: string;
}) {
  const [open, setOpen] = useState(false);

  // Escape closes the drawer.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className={`relative ${className ?? ""}`}>
      <button
        type="button"
        aria-label="Menu"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls="mobile-nav-panel"
        onClick={() => setOpen((o) => !o)}
        className="flex h-10 w-10 items-center justify-center rounded-md border border-hairline text-ink-2 transition-colors hover:bg-hover hover:text-ink"
      >
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          aria-hidden
        >
          {open ? (
            <>
              <line x1="5" y1="5" x2="19" y2="19" />
              <line x1="19" y1="5" x2="5" y2="19" />
            </>
          ) : (
            <>
              <line x1="3" y1="6" x2="21" y2="6" />
              <line x1="3" y1="12" x2="21" y2="12" />
              <line x1="3" y1="18" x2="21" y2="18" />
            </>
          )}
        </svg>
      </button>

      {open && (
        <>
          {/* Tap-outside-to-close backdrop. */}
          <button
            type="button"
            aria-hidden
            tabIndex={-1}
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-30 cursor-default"
          />
          <div
            id="mobile-nav-panel"
            role="menu"
            className="absolute right-0 top-[calc(100%+0.5rem)] z-40 w-56 max-w-[80vw] rounded-lg border border-hairline bg-surface/95 p-2 backdrop-blur"
          >
            <nav aria-label="Primary" className="flex flex-col">
              {tabs.map((t) => (
                <Link
                  key={t.href}
                  href={t.href}
                  role="menuitem"
                  aria-current={t.href === active ? "page" : undefined}
                  onClick={() => setOpen(false)}
                  className={`flex min-h-[44px] items-center rounded-md px-3 text-sm transition-colors hover:bg-hover ${
                    t.href === active
                      ? "font-medium text-ink"
                      : "text-ink-2 hover:text-ink"
                  }`}
                >
                  {t.label}
                </Link>
              ))}
            </nav>
            <div className="mt-2 border-t border-hairline px-3 pt-2">
              <p className="truncate text-[12px] text-ink-3">
                {email} ({role ?? "no role"})
              </p>
              {signOut && (
                <form action={signOut}>
                  <button
                    type="submit"
                    className="mt-1 flex min-h-[44px] items-center text-sm text-ink-2 transition-colors hover:text-ink"
                  >
                    Sign out
                  </button>
                </form>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

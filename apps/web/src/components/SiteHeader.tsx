import NavTabs, { type NavRoute } from "@/components/NavTabs";

/** Slim sticky nav on a blurred surface — the standard page banner
 * (app name + tabs). The dashboard renders its own variant with the
 * email/sign-out cluster on the right. */
export default function SiteHeader({ active }: { active: NavRoute }) {
  return (
    <header className="sticky top-0 z-10 border-b border-hairline bg-surface/80 backdrop-blur">
      <div className="mx-auto flex h-12 w-full max-w-5xl items-center gap-6 px-6">
        <span className="text-sm font-semibold text-ink">broker-cockpit</span>
        <NavTabs active={active} />
      </div>
    </header>
  );
}

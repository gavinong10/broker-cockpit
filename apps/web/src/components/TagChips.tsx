import Link from "next/link";

// Tiny theme-tag chips (ai, cpo-optics, data-center, ...). Pure labels —
// no dollar information, so they render identically for masked viewers.
// Chips link to the tag-filtered Exposure view; the active tag links back
// to the unfiltered view (toggle-off).
export default function TagChips({
  tags,
  activeTag,
}: {
  tags?: string[];
  activeTag?: string | null;
}) {
  if (!tags || tags.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {tags.map((t) => {
        const active = t === activeTag;
        return (
          <Link
            key={t}
            href={active ? "/exposure" : `/exposure?tag=${encodeURIComponent(t)}`}
            className={
              active
                ? "rounded-full border border-accent px-1.5 py-px text-[10px] leading-4 text-accent"
                : "rounded-full border border-hairline px-1.5 py-px text-[10px] leading-4 text-ink-3 hover:border-ink-3"
            }
          >
            {t}
          </Link>
        );
      })}
    </span>
  );
}

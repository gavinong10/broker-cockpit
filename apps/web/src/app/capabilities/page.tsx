// Owner-only Capabilities page: renders every *.md in docs/capabilities as a
// section. The directory is read AT REQUEST TIME so new capability docs show
// up without a rebuild — in the container it's the read-only compose mount
// /srv/docs/capabilities; in local `npm run dev` it falls back to the
// repo-relative path.

import fs from "node:fs/promises";
import path from "node:path";
import ReactMarkdown, { type Components } from "react-markdown";
import { auth } from "@/auth";
import type { Role } from "@/lib/roles";
import SiteHeader from "@/components/SiteHeader";

// fs reads must happen per request, never at build time.
export const dynamic = "force-dynamic";

const DOC_DIRS = [
  "/srv/docs/capabilities", // compose ro-mount (container WORKDIR is /srv)
  // Local-dev fallback so `npm run dev` works without the mount.
  path.resolve(process.cwd(), "..", "..", "docs", "capabilities"),
];

type Doc = { file: string; title: string; markdown: string };

async function loadDocs(): Promise<Doc[]> {
  for (const dir of DOC_DIRS) {
    let files: string[];
    try {
      files = await fs.readdir(dir);
    } catch {
      continue; // dir absent in this environment — try the next candidate
    }
    const mdFiles = files.filter((f) => f.endsWith(".md")).sort();
    return Promise.all(
      mdFiles.map(async (file) => {
        const raw = await fs.readFile(path.join(dir, file), "utf8");
        const heading = raw.match(/^#\s+(.+)$/m);
        // The first # heading becomes the section title; drop it from the
        // body so it isn't rendered twice.
        const markdown = heading ? raw.replace(heading[0], "") : raw;
        return { file, title: heading?.[1].trim() ?? file, markdown };
      }),
    );
  }
  return [];
}

// Typography for react-markdown matched to the dark theme tokens.
// No raw-HTML rendering, no plugins — react-markdown's safe defaults.
const mdComponents: Components = {
  h1: ({ children }) => (
    <h3 className="mt-6 text-base font-semibold text-ink first:mt-0">
      {children}
    </h3>
  ),
  h2: ({ children }) => (
    <h4 className="mt-6 text-sm font-semibold text-ink first:mt-0">
      {children}
    </h4>
  ),
  h3: ({ children }) => (
    <h5 className="mt-4 text-sm font-medium text-ink first:mt-0">
      {children}
    </h5>
  ),
  p: ({ children }) => (
    <p className="mt-3 text-sm leading-6 text-ink-2 first:mt-0">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="mt-3 list-disc space-y-1 pl-5 text-sm leading-6 text-ink-2">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm leading-6 text-ink-2">
      {children}
    </ol>
  ),
  a: ({ href, children }) => (
    <a href={href} className="text-accent underline underline-offset-2">
      {children}
    </a>
  ),
  code: ({ children }) => (
    <code className="rounded bg-card px-1 py-0.5 font-mono text-[0.8rem] text-ink">
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="mt-3 overflow-x-auto rounded-lg border border-hairline bg-card p-3 text-xs leading-5 text-ink [&_code]:bg-transparent [&_code]:p-0">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="mt-3 border-l-2 border-hairline pl-3 text-sm italic text-ink-2">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-6 border-hairline" />,
  table: ({ children }) => (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-hairline px-2 py-1 text-left font-medium text-ink">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-hairline px-2 py-1 text-ink-2">
      {children}
    </td>
  ),
};

function PageHeader({ role }: { role: Role }) {
  return <SiteHeader role={role} active="/capabilities" />;
}

export default async function CapabilitiesPage() {
  const session = await auth();
  const role: Role =
    (session?.user as { role?: Role } | undefined)?.role ?? null;

  if (role !== "owner") {
    // Docs contain operational details viewers must not see.
    return (
      <>
        <PageHeader role={role} />
        <main className="mx-auto flex w-full max-w-2xl flex-col gap-10 px-6 py-10 font-sans">
          <p className="text-sm text-ink-2">
            Not available — this page is owner-only.
          </p>
        </main>
      </>
    );
  }

  const docs = await loadDocs();

  return (
    <>
      <PageHeader role={role} />
      {/* Readable prose measure. */}
      <main className="mx-auto flex w-full max-w-2xl flex-col gap-10 px-6 py-10 font-sans">
        {docs.length === 0 && (
          <p className="text-sm text-ink-2">
            No capability docs found. Add markdown files to docs/capabilities/
            (mounted at /srv/docs/capabilities in the container).
          </p>
        )}

        {docs.map((doc) => (
          <section key={doc.file} aria-label={doc.title}>
            <h2 className="text-lg font-semibold tracking-tight text-ink">
              {doc.title}
            </h2>
            <p className="mt-1 font-mono text-xs text-ink-3">{doc.file}</p>
            <div className="mt-4">
              <ReactMarkdown components={mdComponents}>
                {doc.markdown}
              </ReactMarkdown>
            </div>
          </section>
        ))}
      </main>
    </>
  );
}

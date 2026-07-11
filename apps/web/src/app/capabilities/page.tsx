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
import NavTabs from "@/components/NavTabs";

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

// Typography for react-markdown consistent with the app's zinc/dark palette.
// No raw-HTML rendering, no plugins — react-markdown's safe defaults.
const mdComponents: Components = {
  h1: ({ children }) => (
    <h3 className="mt-6 text-base font-semibold text-zinc-950 first:mt-0 dark:text-zinc-50">
      {children}
    </h3>
  ),
  h2: ({ children }) => (
    <h4 className="mt-6 text-sm font-semibold text-zinc-950 first:mt-0 dark:text-zinc-50">
      {children}
    </h4>
  ),
  h3: ({ children }) => (
    <h5 className="mt-4 text-sm font-medium text-zinc-950 first:mt-0 dark:text-zinc-50">
      {children}
    </h5>
  ),
  p: ({ children }) => (
    <p className="mt-3 text-sm leading-6 text-zinc-700 first:mt-0 dark:text-zinc-300">
      {children}
    </p>
  ),
  ul: ({ children }) => (
    <ul className="mt-3 list-disc space-y-1 pl-5 text-sm leading-6 text-zinc-700 dark:text-zinc-300">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm leading-6 text-zinc-700 dark:text-zinc-300">
      {children}
    </ol>
  ),
  a: ({ href, children }) => (
    <a
      href={href}
      className="text-zinc-950 underline underline-offset-2 dark:text-zinc-50"
    >
      {children}
    </a>
  ),
  code: ({ children }) => (
    <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-[0.8rem] text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200">
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="mt-3 overflow-x-auto rounded-md border border-zinc-200 bg-zinc-50 p-3 text-xs leading-5 dark:border-zinc-800 dark:bg-zinc-900 [&_code]:bg-transparent [&_code]:p-0">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="mt-3 border-l-2 border-zinc-300 pl-3 text-sm italic text-zinc-600 dark:border-zinc-700 dark:text-zinc-400">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-6 border-zinc-200 dark:border-zinc-800" />,
  table: ({ children }) => (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-zinc-300 px-2 py-1 text-left font-medium text-zinc-950 dark:border-zinc-700 dark:text-zinc-50">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-zinc-200 px-2 py-1 text-zinc-700 dark:border-zinc-800 dark:text-zinc-300">
      {children}
    </td>
  ),
};

export default async function CapabilitiesPage() {
  const session = await auth();
  const role: Role =
    (session?.user as { role?: Role } | undefined)?.role ?? null;

  if (role !== "owner") {
    // Docs contain operational details viewers must not see.
    return (
      <main className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-6 py-10 font-sans">
        <div className="flex items-center gap-6">
          <h1 className="text-lg font-semibold text-zinc-950 dark:text-zinc-50">
            broker-cockpit
          </h1>
          <NavTabs role={role} active="/capabilities" />
        </div>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          Not available — this page is owner-only.
        </p>
      </main>
    );
  }

  const docs = await loadDocs();

  return (
    <main className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-6 py-10 font-sans">
      <div className="flex items-center gap-6">
        <h1 className="text-lg font-semibold text-zinc-950 dark:text-zinc-50">
          broker-cockpit
        </h1>
        <NavTabs role={role} active="/capabilities" />
      </div>

      {docs.length === 0 && (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          No capability docs found. Add markdown files to docs/capabilities/
          (mounted at /srv/docs/capabilities in the container).
        </p>
      )}

      {docs.map((doc) => (
        <section
          key={doc.file}
          aria-label={doc.title}
          className="rounded-lg border border-zinc-200 p-6 dark:border-zinc-800"
        >
          <h2 className="text-xl font-semibold text-zinc-950 dark:text-zinc-50">
            {doc.title}
          </h2>
          <p className="mt-1 font-mono text-xs text-zinc-500 dark:text-zinc-400">
            {doc.file}
          </p>
          <div className="mt-4">
            <ReactMarkdown components={mdComponents}>
              {doc.markdown}
            </ReactMarkdown>
          </div>
        </section>
      ))}
    </main>
  );
}

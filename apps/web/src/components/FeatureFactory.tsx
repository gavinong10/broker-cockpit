"use client";

import { useActionState, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { createFeature, factoryPause, featureAction, type ActionResult } from "@/app/actions/features";

export type Feature = {
  slug: string;
  prompt: string;
  model: string;
  status: string;
  diff_stat: string | null;
  risky_paths: string[] | null;
  merge_sha: string | null;
  report: string | null;
  created_at: string;
  updated_at: string;
};

const ACTIVE = new Set(["created", "building"]);
const BUILT = "built";
const ACCEPTED = "accepted";

function StatusChip({ status }: { status: string }) {
  const tone =
    status === BUILT ? "border-accent text-accent"
    : status === ACCEPTED ? "border-gain text-gain"
    : status === "reverted" || status === "discarded" ? "border-hairline text-ink-3"
    : status.startsWith("failed") || status === "killed" ? "border-loss text-loss"
    : "border-hairline text-ink-2";
  const label = ACTIVE.has(status) ? "building…" : status.replace(/_/g, " ");
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[11px] uppercase tracking-wide ${tone}`}>
      {label}
    </span>
  );
}

export default function FeatureFactory({
  initialFeatures,
  runnerConfigured,
  runnerPaused,
}: {
  initialFeatures: Feature[];
  runnerConfigured: boolean;
  runnerPaused: boolean;
}) {
  const router = useRouter();
  const [state, submit, pending] = useActionState<ActionResult, FormData>(createFeature, {
    ok: true,
    message: "",
  });
  const [toggling, startToggle] = useTransition();
  const [toggleMsg, setToggleMsg] = useState("");

  function togglePause() {
    startToggle(async () => {
      const r = await factoryPause(!runnerPaused);
      setToggleMsg(r.message);
      router.refresh();
    });
  }

  return (
    <div className="flex flex-col gap-8">
      {!runnerConfigured && (
        <div className="rounded-md border border-loss/50 bg-loss/10 px-4 py-2 text-sm text-loss">
          Build runner not configured on the host — see docs/capabilities/feature-factory.md.
        </div>
      )}

      {runnerConfigured && (
        <div
          className={`flex items-center justify-between gap-3 rounded-md border px-4 py-2 text-sm ${
            runnerPaused ? "border-loss/50 bg-loss/10 text-loss" : "border-hairline bg-card text-ink-2"
          }`}
        >
          <span>
            {runnerPaused
              ? "Factory PAUSED — new builds are refused by the host runner."
              : "Factory active."}
            {toggleMsg && <span className="ml-2 text-ink-3">{toggleMsg}</span>}
          </span>
          <button
            onClick={togglePause}
            disabled={toggling}
            className={`shrink-0 rounded-md border px-3 py-1 text-[13px] font-medium disabled:opacity-50 ${
              runnerPaused
                ? "border-gain/50 text-gain hover:bg-gain/10"
                : "border-loss/50 text-loss hover:bg-loss/10"
            }`}
          >
            {toggling ? "…" : runnerPaused ? "Resume factory" : "Pause factory"}
          </button>
        </div>
      )}

      <form action={submit} className="flex flex-col gap-3 rounded-xl border border-hairline bg-card p-5">
        <label className="micro-label text-ink-2">New feature</label>
        <textarea
          name="prompt"
          rows={4}
          required
          placeholder="e.g. Add a CSV export button to the Exposure tab that downloads the current rows. (Optional first line: 'model: opus')"
          className="w-full resize-y rounded-md border border-hairline bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none"
        />
        <div className="flex items-center justify-between gap-3">
          <select
            name="model"
            defaultValue=""
            className="rounded-md border border-hairline bg-surface px-2 py-1.5 text-sm text-ink"
          >
            <option value="">Default (Fable)</option>
            <option value="fable">Fable</option>
            <option value="opus">Opus</option>
            <option value="sonnet">Sonnet</option>
          </select>
          <button
            type="submit"
            disabled={pending || !runnerConfigured || runnerPaused}
            className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {pending ? "Starting…" : runnerPaused ? "Paused" : "Build feature"}
          </button>
        </div>
        {state.message && (
          <p className={`text-sm ${state.ok ? "text-ink-2" : "text-loss"}`}>{state.message}</p>
        )}
      </form>

      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <h2 className="micro-label text-ink-2">Features</h2>
          <button
            onClick={() => router.refresh()}
            className="text-[13px] text-ink-2 hover:text-ink"
          >
            Refresh ↻
          </button>
        </div>
        {initialFeatures.length === 0 ? (
          <p className="text-sm text-ink-3">No features yet.</p>
        ) : (
          initialFeatures.map((f) => <FeatureRow key={f.slug} f={f} />)
        )}
      </div>
    </div>
  );
}

function FeatureRow({ f }: { f: Feature }) {
  const router = useRouter();
  const [diff, setDiff] = useState<string | null>(null);
  const [busy, startBusy] = useTransition();
  const [msg, setMsg] = useState<string>("");

  const risky = f.risky_paths ?? [];

  async function loadDiff() {
    if (diff !== null) {
      setDiff(null);
      return;
    }
    const res = await fetch(`/features/${encodeURIComponent(f.slug)}/diff`);
    setDiff(res.ok ? await res.text() : "Could not load diff.");
  }

  function act(verb: "accept" | "revert" | "sync" | "kill") {
    startBusy(async () => {
      const r = await featureAction(f.slug, verb);
      setMsg(r.message);
      router.refresh();
    });
  }

  return (
    <div className="rounded-xl border border-hairline bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-ink">{f.slug}</span>
            <StatusChip status={f.status} />
            <span className="text-[11px] text-ink-3">{f.model}</span>
          </div>
          <p className="mt-1 line-clamp-2 text-[13px] text-ink-2" title={f.prompt}>
            {f.prompt}
          </p>
          {f.diff_stat && <p className="mt-1 text-[12px] text-ink-3">{f.diff_stat}</p>}
          {risky.length > 0 && (
            <p className="mt-1 text-[12px] text-loss">
              ⚠ touches sensitive paths: {risky.join(", ")} — review carefully.
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-2">
          {(f.status === BUILT || f.status === ACCEPTED) && (
            <button onClick={loadDiff} className="rounded-md border border-hairline px-3 py-1 text-[13px] text-ink hover:bg-hover">
              {diff !== null ? "Hide diff" : "Preview"}
            </button>
          )}
          {f.status === BUILT && (
            <button
              onClick={() => act("accept")}
              disabled={busy}
              className="rounded-md bg-gain/15 px-3 py-1 text-[13px] font-medium text-gain hover:bg-gain/25 disabled:opacity-50"
            >
              Accept
            </button>
          )}
          {(f.status === BUILT || f.status.startsWith("failed") || f.status === "killed" || f.status === "created") && (
            <button
              onClick={() => act("revert")}
              disabled={busy}
              className="rounded-md border border-hairline px-3 py-1 text-[13px] text-ink-2 hover:bg-hover disabled:opacity-50"
            >
              Discard
            </button>
          )}
          {f.status === ACCEPTED && (
            <button
              onClick={() => act("revert")}
              disabled={busy}
              className="rounded-md border border-loss/50 px-3 py-1 text-[13px] text-loss hover:bg-loss/10 disabled:opacity-50"
            >
              Revert
            </button>
          )}
          {ACTIVE.has(f.status) && (
            <button onClick={() => act("sync")} disabled={busy} className="rounded-md border border-hairline px-3 py-1 text-[13px] text-ink-2 hover:bg-hover disabled:opacity-50">
              Check status
            </button>
          )}
          {f.status === "building" && (
            <button
              onClick={() => act("kill")}
              disabled={busy}
              className="rounded-md border border-loss/50 px-3 py-1 text-[13px] font-medium text-loss hover:bg-loss/10 disabled:opacity-50"
            >
              Stop
            </button>
          )}
        </div>
      </div>
      {msg && <p className="mt-2 text-[12px] text-ink-2">{msg}</p>}
      {f.report && !ACTIVE.has(f.status) && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[12px] text-ink-3">Build report</summary>
          <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-surface p-3 text-[12px] text-ink-2">{f.report}</pre>
        </details>
      )}
      {diff !== null && (
        <pre className="mt-2 max-h-[32rem] overflow-auto rounded-md border border-hairline bg-surface p-3 text-[12px] leading-relaxed text-ink-2">{diff}</pre>
      )}
    </div>
  );
}

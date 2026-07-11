import { exitViewerPreview } from "@/app/actions/view-as";

/** Unmissable strip shown while the owner is previewing the viewer
 * experience. Rendered from the root layout whenever the preview is active. */
export default function ViewerPreviewBar() {
  return (
    <div className="border-b border-amber-500/40 bg-amber-950/60">
      <div className="mx-auto flex h-10 w-full max-w-5xl items-center justify-between gap-4 px-6">
        <p className="text-[13px] text-amber-200">
          Viewer preview — you are seeing exactly what a view-only user sees
          (dollars and quantities masked, owner tools hidden).
        </p>
        <form action={exitViewerPreview}>
          <button
            type="submit"
            className="rounded-full border border-amber-500/50 px-3 py-1 text-[12px] font-medium text-amber-200 transition-colors hover:bg-amber-900/50"
          >
            Exit preview
          </button>
        </form>
      </div>
    </div>
  );
}

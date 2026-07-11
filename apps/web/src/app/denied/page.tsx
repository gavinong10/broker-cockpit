export default function DeniedPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 font-sans">
      <div className="flex w-full max-w-sm flex-col items-center gap-3 rounded-xl border border-hairline bg-card px-8 py-12 text-center">
        <h1 className="text-xl font-semibold tracking-tight text-ink">
          Access denied
        </h1>
        <p className="text-sm text-ink-2">This instance is invite-only.</p>
      </div>
    </main>
  );
}

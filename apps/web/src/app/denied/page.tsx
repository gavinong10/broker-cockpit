export default function DeniedPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-zinc-50 font-sans dark:bg-black">
      <h1 className="text-2xl font-semibold text-black dark:text-zinc-50">Access denied</h1>
      <p className="text-zinc-600 dark:text-zinc-400">This instance is invite-only.</p>
    </main>
  );
}

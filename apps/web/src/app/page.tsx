import { auth, signOut } from "../auth";

export default async function Home() {
  const session = await auth();
  const email = session?.user?.email ?? "unknown";
  const role = (session?.user as { role?: "owner" | "viewer" | null } | undefined)?.role ?? null;

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-zinc-50 font-sans dark:bg-black">
      <h1 className="text-2xl font-semibold text-black dark:text-zinc-50">broker-cockpit</h1>
      <p className="text-zinc-600 dark:text-zinc-400">
        Signed in as {email} ({role ?? "no role"})
      </p>
      <form
        action={async () => {
          "use server";
          await signOut({ redirectTo: "/login" });
        }}
      >
        <button
          type="submit"
          className="rounded-md border border-zinc-300 px-4 py-2 text-sm text-black dark:border-zinc-700 dark:text-zinc-50"
        >
          Sign out
        </button>
      </form>
    </main>
  );
}

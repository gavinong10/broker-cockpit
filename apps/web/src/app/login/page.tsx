import { signIn } from "../../auth";

export default function LoginPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-zinc-50 font-sans dark:bg-black">
      <h1 className="text-2xl font-semibold text-black dark:text-zinc-50">broker-cockpit</h1>
      <form
        action={async () => {
          "use server";
          await signIn("google", { redirectTo: "/" });
        }}
      >
        <button
          type="submit"
          className="rounded-md bg-black px-6 py-3 text-white dark:bg-zinc-50 dark:text-black"
        >
          Sign in with Google
        </button>
      </form>
    </main>
  );
}

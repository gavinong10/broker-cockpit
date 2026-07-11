import { listAccessEvents, listUsers } from "@/db";
import { groupAccessHistory } from "@/lib/access";
import AccessHistory from "@/components/AccessHistory";
import { enterViewerPreview } from "@/app/actions/view-as";
import { getViewerContext } from "@/lib/viewerContext";
import SiteHeader from "@/components/SiteHeader";
import UserAdmin from "@/components/UserAdmin";

export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const { role } = await getViewerContext();

  if (role !== "owner") {
    return (
      <>
        <SiteHeader role={role} active="/admin" />
        <main className="mx-auto flex w-full max-w-2xl flex-col gap-10 px-6 py-10 font-sans">
          <p className="text-sm text-ink-2">Not available — this page is owner-only.</p>
        </main>
      </>
    );
  }

  const users = await listUsers();
  const events = await listAccessEvents();
  const { byUser, other } = groupAccessHistory(
    events,
    users.map((u) => u.email),
    new Date(),
  );

  return (
    <>
      <SiteHeader role={role} active="/admin" />
      <main className="mx-auto flex w-full max-w-2xl flex-col gap-10 px-6 py-10 font-sans">
        <div>
          <h1 className="text-xl font-semibold text-ink">Users</h1>
          <p className="mt-1 text-sm text-ink-2">
            Who can sign in, and what they can see.
          </p>
        </div>
        <UserAdmin users={users} />

        <AccessHistory byUser={Array.from(byUser.entries())} other={other} />

        <section className="rounded-xl border border-hairline bg-card p-5">
          <h2 className="micro-label">Verify what viewers see</h2>
          <p className="mt-2 text-sm text-ink-2">
            Switches your own session to the exact view-only rendering —
            dollars and quantities masked, owner tools hidden — so you can
            check for information leaks before inviting someone. An amber bar
            with an exit button stays visible while active.
          </p>
          <form action={enterViewerPreview} className="mt-3">
            <button
              type="submit"
              className="rounded-full border border-hairline px-4 py-1.5 text-sm text-ink transition-colors hover:bg-hover"
            >
              Preview as viewer
            </button>
          </form>
        </section>
      </main>
    </>
  );
}

import { auth } from "@/auth";
import { listUsers } from "@/db";
import type { Role } from "@/lib/roles";
import SiteHeader from "@/components/SiteHeader";
import UserAdmin from "@/components/UserAdmin";

export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const session = await auth();
  const role: Role = (session?.user as { role?: Role } | undefined)?.role ?? null;

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
      </main>
    </>
  );
}

import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import { getUserFlags, getUserRole, pool } from "./db";

async function auditAuthEvent(actor: string, category: string, payload: object) {
  try {
    await pool.query(
      "INSERT INTO audit_log (actor, category, payload) VALUES ($1, $2, $3)",
      [actor, category, JSON.stringify(payload)],
    );
  } catch (e) {
    // Audit logging must never take down the auth flow itself.
    console.error("audit_log insert failed", e);
  }
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [Google],
  session: { strategy: "jwt" },
  trustHost: true, // self-hosted behind compose/caddy; only web is exposed
  callbacks: {
    async signIn({ user }) {
      const email = user.email ?? "";
      const role = await getUserRole(email);
      if (role === null) {
        await auditAuthEvent(email || "unknown", "auth.rejected", { reason: "not-allowlisted" });
        return false; // not allowlisted -> rejected
      }
      await auditAuthEvent(email, "auth.login", { role });
      return true;
    },
    async jwt({ token }) {
      if (token.email) {
        const flags = await getUserFlags(token.email);
        const t = token as { role?: "owner" | "viewer" | null; mask_amounts?: boolean };
        t.role = flags?.role ?? null;
        t.mask_amounts = flags?.mask_amounts ?? false;
      }
      return token;
    },
    async session({ session, token }) {
      const u = session.user as {
        role?: "owner" | "viewer" | null;
        mask_amounts?: boolean;
      };
      u.role = (token.role as "owner" | "viewer" | null | undefined) ?? null;
      u.mask_amounts = (token.mask_amounts as boolean | undefined) ?? false;
      return session;
    },
    authorized: ({ auth }) => !!auth?.user,
  },
  pages: { signIn: "/login", error: "/denied" },
});

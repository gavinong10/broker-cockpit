import { Pool } from "pg";

export const pool = new Pool({ connectionString: process.env.WEB_DATABASE_URL });

export async function getUserRole(email: string): Promise<"owner" | "viewer" | null> {
  const r = await pool.query("SELECT role FROM users WHERE email = $1", [email]);
  return r.rows[0]?.role ?? null;
}

export type UserFlags = { role: "owner" | "viewer"; mask_amounts: boolean };

/** Role + masking flag in one query; null when not allowlisted. */
export async function getUserFlags(email: string): Promise<UserFlags | null> {
  const r = await pool.query(
    "SELECT role, mask_amounts FROM users WHERE email = $1",
    [email],
  );
  const row = r.rows[0];
  return row ? { role: row.role, mask_amounts: row.mask_amounts } : null;
}

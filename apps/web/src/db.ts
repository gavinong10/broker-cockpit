import { Pool } from "pg";

export const pool = new Pool({ connectionString: process.env.WEB_DATABASE_URL });

export async function getUserRole(email: string): Promise<"owner" | "viewer" | null> {
  const r = await pool.query("SELECT role FROM users WHERE email = $1", [email]);
  return r.rows[0]?.role ?? null;
}

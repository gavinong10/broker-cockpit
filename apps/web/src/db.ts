import { Pool } from "pg";

export const pool = new Pool({ connectionString: process.env.WEB_DATABASE_URL });

export async function getUserRole(email: string): Promise<"owner" | "viewer" | null> {
  const r = await pool.query("SELECT role FROM users WHERE email = $1", [email]);
  return r.rows[0]?.role ?? null;
}

export type UserFlags = { role: "owner" | "viewer"; mask_amounts: boolean };

export type UserRow = {
  id: number;
  email: string;
  role: "owner" | "viewer";
  mask_amounts: boolean;
  created_at: string;
};

export async function listUsers(): Promise<UserRow[]> {
  const r = await pool.query(
    "SELECT id, email, role, mask_amounts, created_at FROM users ORDER BY role, id",
  );
  return r.rows;
}

/** Insert a viewer (masked by default, per the dollars-are-owner-only policy). */
export async function insertViewer(email: string): Promise<boolean> {
  const r = await pool.query(
    "INSERT INTO users (email, role, mask_amounts) VALUES ($1, 'viewer', true) " +
      "ON CONFLICT (email) DO NOTHING",
    [email],
  );
  return (r.rowCount ?? 0) > 0;
}

/** Delete a viewer by email. Refuses owners at the SQL level as a backstop. */
export async function deleteViewer(email: string): Promise<boolean> {
  const r = await pool.query(
    "DELETE FROM users WHERE email = $1 AND role = 'viewer'",
    [email],
  );
  return (r.rowCount ?? 0) > 0;
}

export async function auditFromWeb(actor: string, category: string, payload: object) {
  try {
    await pool.query(
      "INSERT INTO audit_log (actor, category, payload) VALUES ($1, $2, $3)",
      [actor, category, JSON.stringify(payload)],
    );
  } catch (e) {
    console.error("audit_log insert failed", e);
  }
}

/** Role + masking flag in one query; null when not allowlisted. */
export async function getUserFlags(email: string): Promise<UserFlags | null> {
  const r = await pool.query(
    "SELECT role, mask_amounts FROM users WHERE email = $1",
    [email],
  );
  const row = r.rows[0];
  return row ? { role: row.role, mask_amounts: row.mask_amounts } : null;
}

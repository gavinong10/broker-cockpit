export type Role = "owner" | "viewer" | null;
export const canRead = (r: Role) => r === "owner" || r === "viewer";
export const canWrite = (r: Role) => r === "owner";

/** Dollar amounts are owner-only. Non-owners are ALWAYS masked, regardless of
 * their mask_amounts flag; the flag remains as an extra owner-side toggle. */
export const isMasked = (r: Role, maskFlag: boolean | undefined) =>
  r !== "owner" || (maskFlag ?? false);

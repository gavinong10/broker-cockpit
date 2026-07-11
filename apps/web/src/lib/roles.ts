export type Role = "owner" | "viewer" | null;
export const canRead = (r: Role) => r === "owner" || r === "viewer";
export const canWrite = (r: Role) => r === "owner";

/** Owner-only ACTION gates. Pages render read-only for viewers; these gate
 * the mutations. The real boundary is each server action re-checking the
 * REAL session role via auth() — never the rendered/effective view. */
export const canOperateFactory = (r: Role) => r === "owner";
export const canManageUsers = (r: Role) => r === "owner";

/** The one message every owner-only action returns to non-owners. */
export const PERMISSION_DENIED_MESSAGE = "You don't have permission — owner only.";

/** Dollar amounts are owner-only. Non-owners are ALWAYS masked, regardless of
 * their mask_amounts flag; the flag remains as an extra owner-side toggle. */
export const isMasked = (r: Role, maskFlag: boolean | undefined) =>
  r !== "owner" || (maskFlag ?? false);

export type ViewerView = { role: Role; masked: boolean; previewing: boolean };

/** Owner's "view as viewer" preview: strictly a DOWNGRADE. The preview cookie
 * only has an effect for a real owner (anyone else already gets the viewer
 * treatment); it can never unmask or elevate. */
export const effectiveView = (
  realRole: Role,
  maskFlag: boolean | undefined,
  previewCookie: boolean,
): ViewerView => {
  if (realRole === "owner" && previewCookie) {
    return { role: "viewer", masked: true, previewing: true };
  }
  return { role: realRole, masked: isMasked(realRole, maskFlag), previewing: false };
};

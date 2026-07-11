export type Role = "owner" | "viewer" | null;
export const canRead = (r: Role) => r === "owner" || r === "viewer";
export const canWrite = (r: Role) => r === "owner";

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

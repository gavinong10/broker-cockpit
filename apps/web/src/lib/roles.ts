export type Role = "owner" | "viewer" | null;
export const canRead = (r: Role) => r === "owner" || r === "viewer";
export const canWrite = (r: Role) => r === "owner";

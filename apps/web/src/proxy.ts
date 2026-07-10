// Next 16 renamed the `middleware` file convention to `proxy`.
// Every route requires a session except login/denied/auth endpoints and static assets;
// the `authorized` callback in auth.ts redirects unauthenticated requests to /login.
export { auth as proxy } from "./auth";

export const config = {
  matcher: ["/((?!api/auth|login|denied|_next|favicon.ico).*)"],
};

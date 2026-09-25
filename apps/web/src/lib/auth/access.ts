/*
 * Pure access-control decision used by the proxy (every request), the
 * authenticated layout (defence in depth) and the login flow (where to go next).
 * No I/O here, so it is unit-tested in access.test.ts.
 *
 * Policy:
 * - /login and /mfa/* are the only app routes reachable without a full (aal2) session.
 * - Every other route requires a signed-in user whose session is aal2.
 * - An aal1 session goes to /mfa/verify if the user has a verified factor
 *   (nextLevel === "aal2"), otherwise to /mfa/enroll: MFA is always required.
 * - If an allowed email is configured and the user's email differs, the
 *   session is signed out and the user is sent to /login with an error.
 */

export type AAL = "aal1" | "aal2";

export type AccessInput = {
  user: { email?: string | null } | null;
  currentLevel: AAL | null | undefined;
  nextLevel: AAL | null | undefined;
  pathname: string;
  allowedEmail?: string | null;
};

export type AccessDecision =
  | { action: "allow" }
  | { action: "redirect"; to: string; signOut?: boolean };

export const HOME_PATH = "/";
export const LOGIN_PATH = "/login";
export const MFA_ENROLL_PATH = "/mfa/enroll";
export const MFA_VERIFY_PATH = "/mfa/verify";
export const NOT_ALLOWED_PATH = `${LOGIN_PATH}?error=not_allowed`;

function normalisePath(pathname: string): string {
  if (pathname.length > 1 && pathname.endsWith("/")) return pathname.replace(/\/+$/, "") || "/";
  return pathname;
}

function isLoginPath(path: string) {
  return path === LOGIN_PATH;
}

function isMfaPath(path: string) {
  return path === "/mfa" || path.startsWith("/mfa/");
}

/** Routes that render without a full aal2 session (the auth flow itself). */
export function isAuthFlowPath(pathname: string): boolean {
  const path = normalisePath(pathname);
  return isLoginPath(path) || isMfaPath(path);
}

export function isEmailAllowed(
  email: string | null | undefined,
  allowedEmail: string | null | undefined,
): boolean {
  if (!allowedEmail) return true;
  if (!email) return false;
  return email.trim().toLowerCase() === allowedEmail.trim().toLowerCase();
}

/** Where an aal1 session must go to reach aal2. */
export function mfaPathFor(nextLevel: AAL | null | undefined): string {
  return nextLevel === "aal2" ? MFA_VERIFY_PATH : MFA_ENROLL_PATH;
}

export function decideAccess(input: AccessInput): AccessDecision {
  const path = normalisePath(input.pathname);
  const { user, currentLevel, nextLevel, allowedEmail } = input;

  if (!user) {
    return isLoginPath(path) ? { action: "allow" } : { action: "redirect", to: LOGIN_PATH };
  }

  if (!isEmailAllowed(user.email, allowedEmail)) {
    return { action: "redirect", to: NOT_ALLOWED_PATH, signOut: true };
  }

  if (currentLevel === "aal2") {
    // Fully authenticated: the auth-flow pages have nothing left to do.
    return isAuthFlowPath(path) ? { action: "redirect", to: HOME_PATH } : { action: "allow" };
  }

  // Signed in with one factor only (aal1 or unknown): MFA is mandatory.
  const target = mfaPathFor(nextLevel);
  return path === target ? { action: "allow" } : { action: "redirect", to: target };
}

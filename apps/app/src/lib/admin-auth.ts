import { timingSafeEqual } from "node:crypto";

// The gate sends admin traffic through oauth2-proxy (Google login) and then adds X-Admin-Gate.
// Public traffic has both headers stripped by the gate, so they cannot be forged from outside.

function sameSecret(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

export function allowedEmails(): string[] {
  return (process.env.ADMIN_EMAILS ?? "")
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);
}

/** The signed-in admin's email, or null when the request did not come through the login. */
export function adminEmail(headers: Headers): string | null {
  const secret = process.env.ADMIN_GATE_SECRET ?? "";
  const gate = headers.get("x-admin-gate") ?? "";
  if (secret.length < 32 || !sameSecret(gate, secret)) return null;
  const email = (headers.get("x-forwarded-email") ?? "").trim().toLowerCase();
  return email && allowedEmails().includes(email) ? email : null;
}

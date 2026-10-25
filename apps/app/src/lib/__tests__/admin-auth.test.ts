import { afterEach, describe, expect, it, vi } from "vitest";
import { adminEmail } from "../admin-auth";

const SECRET = "s".repeat(40);
const h = (o: Record<string, string>) => new Headers(o);

describe("adminEmail", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("accepts only the gate secret plus an allowed Google email", () => {
    vi.stubEnv("ADMIN_GATE_SECRET", SECRET);
    vi.stubEnv("ADMIN_EMAILS", "Admin@Example.com");
    expect(adminEmail(h({ "x-admin-gate": SECRET, "x-forwarded-email": "admin@example.com" }))).toBe("admin@example.com");
    expect(adminEmail(h({ "x-admin-gate": SECRET, "x-forwarded-email": "other@example.com" }))).toBeNull();
    expect(adminEmail(h({ "x-admin-gate": "wrong", "x-forwarded-email": "admin@example.com" }))).toBeNull();
    expect(adminEmail(h({ "x-forwarded-email": "admin@example.com" }))).toBeNull();
  });

  it("is closed when the secret is missing or short", () => {
    vi.stubEnv("ADMIN_GATE_SECRET", "short");
    vi.stubEnv("ADMIN_EMAILS", "admin@example.com");
    expect(adminEmail(h({ "x-admin-gate": "short", "x-forwarded-email": "admin@example.com" }))).toBeNull();
  });
});

import type { Page } from "@playwright/test";

/** Sign up a fresh member through the real local API (its own rate-limit bucket), leaving the page signed in. */
export async function signUp(page: Page, tag = "e2e"): Promise<string> {
  const mail = `${tag}${Date.now()}${Math.floor(Math.random() * 1e6)}@example.com`;
  const ip = `198.18.${Math.floor(Math.random() * 250)}.${Math.floor(Math.random() * 250)}`;
  const r = await page.request.post("/api/auth/register", {
    data: { email: mail, password: "e2e-pass-12345", accept_terms: true, terms_version: "2026-10-04", privacy_version: "2026-10-04" },
    headers: { "x-forwarded-for": ip },
  });
  if (r.status() !== 201) throw new Error(`sign-up failed: ${r.status()} ${await r.text()}`);
  return mail;
}

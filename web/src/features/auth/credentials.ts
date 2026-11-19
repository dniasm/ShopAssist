/** What the sign-in and sign-up forms check before sending, with the server's own limits (the server checks again).
 *  Pure, so the rules are unit-tested; the dialog only shows the first problem found. Rules follow day7's accounts. */

export const MIN_PASSWORD = 8;
export const MAX_PASSWORD = 256;
const EMAIL = /^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$/;

export type Mode = "signin" | "signup";

export interface CredentialsForm {
  name: string;
  email: string;
  password: string;
  invite: string;
}

export const EMPTY_FORM: CredentialsForm = { name: "", email: "", password: "", invite: "" };

/** The first problem with the form, or null when it can be sent. Sign-in only needs both fields filled. */
export function formProblem(mode: Mode, form: CredentialsForm, needsInvite: boolean): string | null {
  const email = form.email.trim();
  if (mode === "signup" && !form.name.trim()) return "Enter your name.";
  if (!email) return "Enter your email address.";
  if (mode === "signup" && (email.length > 254 || !EMAIL.test(email))) return "That doesn't look like an email address.";
  if (!form.password) return "Enter your password.";
  if (mode === "signup" && form.password.length < MIN_PASSWORD) return `Use a password of at least ${MIN_PASSWORD} characters.`;
  if (form.password.length > MAX_PASSWORD) return `A password is at most ${MAX_PASSWORD} characters.`;
  if (mode === "signup" && needsInvite && !form.invite.trim()) return "Enter the invite code.";
  return null;
}

/** The seeded demo customers (data/seed/v2/customers.json), in id order. They share DEMO_PASSWORD (README, "Sign-in"). */
export const DEMO_PASSWORD = "shopassist-demo";
export const DEMO_ACCOUNTS = [
  { name: "Priya Raman", email: "priya.raman@example.com" },
  { name: "Tomas Lindqvist", email: "tomas.lindqvist@example.com" },
  { name: "Hana Mori", email: "hana.mori@example.com" },
  { name: "Daniel Osei", email: "daniel.osei@example.com" },
  { name: "Lucia Ferrand", email: "lucia.ferrand@example.com" },
  { name: "Samuel Adeyemi", email: "samuel.adeyemi@example.com" },
] as const;

/** Which demo account the next "Use the demo account" click fills in: the first click Priya, the second Tomas, and so
 *  on, back to Priya after the last. `current` is the index shown now, or null before the first click. */
export function nextDemoIndex(current: number | null): number {
  return current === null ? 0 : (current + 1) % DEMO_ACCOUNTS.length;
}

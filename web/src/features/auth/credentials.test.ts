import { describe, expect, it } from "vitest";
import { DEMO_ACCOUNTS, EMPTY_FORM, MIN_PASSWORD, formProblem, nextDemoIndex } from "./credentials";

const signIn = { ...EMPTY_FORM, email: "ada@example.test", password: "x" };
const signUp = { ...EMPTY_FORM, name: "Ada", email: "ada@example.test", password: "long-enough" };

describe("formProblem", () => {
  it("lets a sign-in through with any non-empty password (older rules are the server's business)", () => {
    expect(formProblem("signin", signIn, false)).toBeNull();
    expect(formProblem("signin", { ...signIn, email: "  " }, false)).toBe("Enter your email address.");
    expect(formProblem("signin", { ...signIn, password: "" }, false)).toBe("Enter your password.");
  });

  it("checks a sign-up's name, email shape and password length, in that order", () => {
    expect(formProblem("signup", signUp, false)).toBeNull();
    expect(formProblem("signup", { ...signUp, name: " " }, false)).toBe("Enter your name.");
    expect(formProblem("signup", { ...signUp, email: "ada@nowhere" }, false)).toBe("That doesn't look like an email address.");
    expect(formProblem("signup", { ...signUp, password: "x".repeat(MIN_PASSWORD - 1) }, false)).toMatch(/at least 8/);
    expect(formProblem("signup", { ...signUp, password: "x".repeat(MIN_PASSWORD) }, false)).toBeNull();
  });

  it("asks for the invite code only when the shop needs one", () => {
    expect(formProblem("signup", signUp, true)).toBe("Enter the invite code.");
    expect(formProblem("signup", { ...signUp, invite: "code" }, true)).toBeNull();
    expect(formProblem("signin", signIn, true)).toBeNull();
  });

  it("caps the password length at the server's limit", () => {
    expect(formProblem("signin", { ...signIn, password: "x".repeat(257) }, false)).toMatch(/at most 256/);
  });
});

describe("nextDemoIndex", () => {
  it("steps through the demo accounts one click at a time and wraps around", () => {
    const names: string[] = [];
    let index: number | null = null;
    for (let click = 0; click < DEMO_ACCOUNTS.length + 1; click++) {
      index = nextDemoIndex(index);
      names.push(DEMO_ACCOUNTS[index].name);
    }
    expect(names.slice(0, 3)).toEqual(["Priya Raman", "Tomas Lindqvist", "Hana Mori"]);
    expect(names[DEMO_ACCOUNTS.length - 1]).toBe("Samuel Adeyemi");
    expect(names[DEMO_ACCOUNTS.length]).toBe("Priya Raman");
  });
});

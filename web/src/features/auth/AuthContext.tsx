import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { setAuthToken, setUnauthenticatedHandler } from "../../api/client";
import { createSession, getMe, signUp as createAccount } from "../../api/endpoints";
import type { Session, SignUpInput, User } from "../../api/types";
import { SignInDialog } from "./SignInDialog";
import { loadSession, saveSession } from "./storage";

interface AuthValue {
  user: User | null;
  token: string | null;
  /** Signs in with email and password. Rejects with an ApiError the dialog shows. */
  signIn: (email: string, password: string) => Promise<void>;
  /** Creates an account and signs in as it. Rejects with an ApiError the dialog shows. */
  signUp: (input: SignUpInput) => Promise<void>;
  signOut: () => void;
  /** Opens the sign-in dialog from anywhere. `then` runs once, after a successful sign-in (e.g. finish an Add to cart). */
  openSignIn: (then?: () => void) => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}

// Read once, synchronously, so the first render already knows whether to show the user menu.
const initial = loadSession();
setAuthToken(initial?.token ?? null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState(initial);
  const [pickerOpen, setPickerOpen] = useState(false);
  const afterSignIn = useRef<(() => void) | null>(null);

  const apply = useCallback((next: typeof initial) => {
    setAuthToken(next?.token ?? null);
    saveSession(next);
    setSession(next);
  }, []);

  const signOut = useCallback(() => apply(null), [apply]);

  const finish = useCallback((next: Session) => {
    apply(next);
    setPickerOpen(false);
    const then = afterSignIn.current;
    afterSignIn.current = null;
    // Let React commit the new user first, so whatever continues runs as the signed-in shopper.
    if (then) window.setTimeout(then, 0);
  }, [apply]);

  const signIn = useCallback(async (email: string, password: string) => finish(await createSession(email, password)), [finish]);
  const signUp = useCallback(async (input: SignUpInput) => finish(await createAccount(input)), [finish]);

  const closePicker = useCallback(() => { afterSignIn.current = null; setPickerOpen(false); }, []);

  // A 401 on any authenticated call means the token expired or was revoked: drop the session.
  useEffect(() => {
    setUnauthenticatedHandler(signOut);
    return () => setUnauthenticatedHandler(null);
  }, [signOut]);

  // Confirm a restored session is still good. A network failure keeps it; only a 401 (handled above) clears it.
  useEffect(() => {
    if (!initial) return;
    const controller = new AbortController();
    // A 401 here reaches the unauthenticated handler inside request(); any other failure keeps the session.
    getMe(controller.signal).catch(() => undefined);
    return () => controller.abort();
  }, []);

  const value = useMemo<AuthValue>(() => ({
    user: session?.user ?? null,
    token: session?.token ?? null,
    signIn,
    signUp,
    signOut,
    openSignIn: (then) => { afterSignIn.current = then ?? null; setPickerOpen(true); },
  }), [session, signIn, signUp, signOut]);

  return (
    <AuthContext.Provider value={value}>
      {children}
      <SignInDialog open={pickerOpen} onClose={closePicker} onSignIn={signIn} onSignUp={signUp} />
    </AuthContext.Provider>
  );
}

import { useEffect, useRef, useState, type FormEvent } from "react";
import { getSignups } from "../../api/endpoints";
import { describeError } from "../../api/errors";
import type { SignUpInput, Signups } from "../../api/types";
import { Icon, Mark } from "../../components/Icon";
import { DEMO_ACCOUNTS, DEMO_PASSWORD, EMPTY_FORM, MIN_PASSWORD, formProblem, nextDemoIndex, type CredentialsForm, type Mode } from "./credentials";
import styles from "./SignInDialog.module.css";

interface Props {
  open: boolean;
  onClose: () => void;
  onSignIn: (email: string, password: string) => Promise<void>;
  onSignUp: (input: SignUpInput) => Promise<void>;
}

/** Sign in or create an account. Layout and flow from day7's log-in page, in this shop's tokens: the form on the
 *  left, a quiet panel on the right (hidden on narrow screens). Opened from anywhere through useAuth().openSignIn. */
export function SignInDialog({ open, onClose, onSignIn, onSignUp }: Props) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={styles.dialog}
      aria-labelledby="signin-title"
      onClose={onClose}
      onClick={(e) => { if (e.target === ref.current) onClose(); }}
    >
      {open && <AuthForm onClose={onClose} onSignIn={onSignIn} onSignUp={onSignUp} />}
    </dialog>
  );
}

function AuthForm({ onClose, onSignIn, onSignUp }: Omit<Props, "open">) {
  const [mode, setMode] = useState<Mode>("signin");
  const [form, setForm] = useState<CredentialsForm>(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [signups, setSignups] = useState<Signups>("open");
  const [demo, setDemo] = useState<number | null>(null);  // the demo account last filled in, if any
  const registering = mode === "signup";
  const needsInvite = signups === "invite";

  useEffect(() => {
    const controller = new AbortController();
    getSignups(controller.signal).then(setSignups).catch(() => undefined);  // unknown: the server asks if it must
    return () => controller.abort();
  }, []);

  const set = (field: keyof CredentialsForm) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [field]: e.target.value }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    const problem = formProblem(mode, form, needsInvite);
    if (problem) { setError(problem); return; }
    setError(null);
    setBusy(true);
    try {
      if (registering) {
        await onSignUp({ name: form.name.trim(), email: form.email.trim(), password: form.password, city: "", invite: form.invite.trim() });
      } else {
        await onSignIn(form.email.trim(), form.password);
      }
    } catch (err) {
      setError(describeError(err));
      setBusy(false);
    }
  }

  function fillNextDemo() {
    const index = nextDemoIndex(demo);
    setDemo(index);
    setForm({ ...EMPTY_FORM, email: DEMO_ACCOUNTS[index].email, password: DEMO_PASSWORD });
    setError(null);
  }

  function switchMode() {
    setMode(registering ? "signin" : "signup");
    setError(null);
  }

  return (
    <div className={styles.layout}>
      <section className={styles.formSide}>
        <header className={styles.head}>
          <div>
            <h2 id="signin-title" className={styles.title}>{registering ? "Create your account" : "Sign in"}</h2>
            <p className="muted">
              {registering ? "Your cart, orders and assistant chats stay with your account." : "Welcome back. Sign in to see your cart and orders."}
            </p>
          </div>
          <button type="button" className="btn icon" onClick={onClose} aria-label="Close"><Icon name="close" /></button>
        </header>

        <form className={styles.form} onSubmit={(e) => void submit(e)} noValidate>
          {registering && (
            <label className={styles.label}>
              Name
              <input className="field" autoComplete="name" maxLength={64} value={form.name} onChange={set("name")} autoFocus />
            </label>
          )}
          <label className={styles.label}>
            Email
            <input className="field" type="email" autoComplete="email" maxLength={254} value={form.email} onChange={set("email")}
              autoFocus={!registering} />
          </label>
          <label className={styles.label}>
            Password
            <input className="field" type="password" autoComplete={registering ? "new-password" : "current-password"} maxLength={256}
              value={form.password} onChange={set("password")} aria-describedby={registering ? "password-rules" : undefined} />
          </label>
          {registering && <p id="password-rules" className={`muted small ${styles.rule}`}>At least {MIN_PASSWORD} characters.</p>}
          {registering && needsInvite && (
            <>
              <label className={styles.label}>
                Invite code
                <input className="field" autoComplete="off" maxLength={128} value={form.invite} onChange={set("invite")}
                  aria-describedby="invite-rules" />
              </label>
              <p id="invite-rules" className={`muted small ${styles.rule}`}>Ask the shop's owner for one.</p>
            </>
          )}

          {error && <p className={styles.error} role="alert">{error}</p>}

          <button type="submit" className={`btn primary large ${styles.submit}`} disabled={busy}>
            {busy ? (registering ? "Creating account…" : "Signing in…") : registering ? "Create account" : "Sign in"}
          </button>
        </form>

        <p className="muted">
          {registering ? "Already have an account?" : "New here?"}{" "}
          <button type="button" className="btn text" onClick={switchMode}>{registering ? "Sign in" : "Create an account"}</button>
        </p>
      </section>

      <aside className={styles.panel} aria-label="About ShopAssist">
        <div className={styles.brand}><Mark size={22} /><span>ShopAssist</span></div>
        <ul className={styles.points}>
          <li><strong>Live orders.</strong> <span className="muted">Watch each order go from confirmed to delivered.</span></li>
          <li><strong>A shopping assistant.</strong> <span className="muted">Ask for products; it never pays without your click.</span></li>
          <li><strong>Your cart, everywhere.</strong> <span className="muted">Saved to your account, not to this browser.</span></li>
        </ul>
        {!registering && (
          <div className={styles.demo}>
            <p className="small"><strong>Trying it out?</strong></p>
            {demo === null ? (
              <p className="muted small">{DEMO_ACCOUNTS.length} demo accounts share the password {DEMO_PASSWORD}.</p>
            ) : (
              <p className="muted small" aria-live="polite">
                Demo account {demo + 1} of {DEMO_ACCOUNTS.length}: {DEMO_ACCOUNTS[demo].name}, {DEMO_ACCOUNTS[demo].email}.
              </p>
            )}
            <button type="button" className="btn" onClick={fillNextDemo}>
              {demo === null ? "Use the demo account" : "Next demo account"}
            </button>
          </div>
        )}
      </aside>
    </div>
  );
}

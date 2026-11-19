import { useAuth } from "../auth/AuthContext";
import styles from "./TurnView.module.css";

/** The card for anything about the cart or orders asked while signed out. */
export function SignInCard() {
  const { openSignIn } = useAuth();
  return (
    <div className={styles.signIn}>
      <button type="button" className="btn primary" onClick={() => openSignIn()}>Sign in</button>
      <span className="muted small">No password: pick a customer to shop as.</span>
    </div>
  );
}

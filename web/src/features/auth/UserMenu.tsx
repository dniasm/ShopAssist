import { useCallback, useId, useRef, useState } from "react";
import { Link } from "react-router";
import { Icon } from "../../components/Icon";
import { useDismiss } from "../../hooks/useDismiss";
import { Avatar } from "./Avatar";
import { useAuth } from "./AuthContext";
import styles from "./UserMenu.module.css";

/** Signed out: a "Sign in" button. Signed in: the person's avatar and name with a small menu (switch user, sign out). */
export function UserMenu() {
  const { user, signOut, openSignIn } = useAuth();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const close = useCallback(() => setOpen(false), []);
  useDismiss(root, open, close);

  if (!user) {
    return (
      <button type="button" className={styles.signIn} onClick={() => openSignIn()} aria-label="Sign in">
        <span className={styles.signInText}>Sign in</span>
        <span className={styles.signInIcon}><Icon name="user" size={18} /></span>
      </button>
    );
  }

  return (
    <div className={styles.root} ref={root}>
      <button
        type="button"
        className={styles.trigger}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`Account menu, signed in as ${user.name}`}
        onClick={() => setOpen((v) => !v)}
      >
        <Avatar userId={user.user_id} name={user.name} />
        <span className={styles.name}>{user.name}</span>
        <Icon name="chevron" size={14} />
      </button>
      {open && (
        <div className={styles.panel} id={panelId}>
          <div className={styles.who}>
            <strong>{user.name}</strong>
            <span className="muted small">{user.email}</span>
            <span className="muted small">{user.city}</span>
          </div>
          <Link to="/orders" className={styles.item} onClick={close}>My orders</Link>
          <button type="button" className={styles.item} onClick={() => { close(); openSignIn(); }}>Switch account</button>
          <button type="button" className={styles.item} onClick={() => { close(); signOut(); }}>Sign out</button>
        </div>
      )}
    </div>
  );
}

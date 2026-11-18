import { useEffect, useRef, useState } from "react";
import { listUsers } from "../../api/endpoints";
import { describeError } from "../../api/errors";
import { ErrorBlock } from "../../components/StateBlocks";
import { useAsyncData } from "../../hooks/useAsyncData";
import { Icon } from "../../components/Icon";
import { Avatar } from "./Avatar";
import styles from "./SignInDialog.module.css";

interface Props {
  open: boolean;
  onClose: () => void;
  onPick: (userId: string) => Promise<void>;
}

/** The demo "Sign in as" picker: no passwords, just a list of the shop's customers. */
export function SignInDialog({ open, onClose, onPick }: Props) {
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
      {open && <Picker onClose={onClose} onPick={onPick} />}
    </dialog>
  );
}

function Picker({ onClose, onPick }: { onClose: () => void; onPick: (userId: string) => Promise<void> }) {
  const users = useAsyncData(listUsers, "users");
  const [pending, setPending] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function choose(userId: string) {
    setPending(userId);
    setFailure(null);
    try {
      await onPick(userId);
    } catch (err) {
      setFailure(describeError(err));
      setPending(null);
    }
  }

  return (
    <div className={styles.body}>
      <header className={styles.head}>
        <div>
          <h2 id="signin-title" className="section-title">Sign in as</h2>
          <p className="muted">Pick a customer to shop as. There are no passwords in this demo.</p>
        </div>
        <button type="button" className="btn icon" onClick={onClose} aria-label="Close"><Icon name="close" /></button>
      </header>

      {users.status === "loading" && (
        <ul className={styles.list} aria-label="Loading customers">
          {[0, 1, 2, 3].map((i) => (
            <li key={i} className={styles.skelRow}><span className="skel" style={{ width: 28, height: 28, borderRadius: 14 }} /><span className="skel" style={{ width: "40%" }} /></li>
          ))}
        </ul>
      )}
      {users.status === "error" && <ErrorBlock error={users.error} what="customers" onRetry={users.reload} />}
      {users.status === "ready" && (
        <ul className={styles.list}>
          {users.data.map((u) => (
            <li key={u.user_id}>
              <button type="button" className={styles.row} disabled={pending !== null} onClick={() => void choose(u.user_id)}>
                <Avatar userId={u.user_id} name={u.name} size={32} />
                <span className={styles.who}>
                  <span className={styles.name}>{u.name}</span>
                  <span className="muted small">{u.city} · {u.email}</span>
                </span>
                {pending === u.user_id && <span className="muted small" role="status">Signing in…</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {failure && <p className={styles.failure} role="alert">{failure}</p>}
    </div>
  );
}

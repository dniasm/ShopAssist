import { useEffect, useState, type FormEvent } from "react";
import { deleteConversation, listConversations, renameConversation } from "../../api/endpoints";
import { describeError } from "../../api/errors";
import type { Conversation } from "../../api/types";
import { cleanTitle, relativeTime } from "./chatTitles";
import styles from "./ChatList.module.css";

interface Props {
  currentId: string;
  onOpen: (id: string) => void;
  onNew: () => void;
  /** A saved chat was deleted (the open one included). */
  onDeleted: (id: string) => void;
}

/** The signed-in shopper's saved chats, newest first: open one, rename it in place, or delete it (after a confirm step). */
export function ChatList({ currentId, onOpen, onNew, onDeleted }: Props) {
  const [items, setItems] = useState<Conversation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    listConversations(ctl.signal).then(setItems, (err: unknown) => { if (!ctl.signal.aborted) setError(describeError(err)); });
    return () => ctl.abort();
  }, []);

  function saveTitle(e: FormEvent, id: string) {
    e.preventDefault();
    const title = cleanTitle(draft);
    if (!title) return;
    setError(null);
    renameConversation(id, title).then(
      (saved) => { setItems((all) => all && all.map((c) => (c.conversation_id === id ? saved : c))); setRenaming(null); },
      (err: unknown) => setError(describeError(err)),
    );
  }

  function remove(id: string) {
    setError(null);
    deleteConversation(id).then(
      () => { setItems((all) => all && all.filter((c) => c.conversation_id !== id)); setDeleting(null); onDeleted(id); },
      (err: unknown) => setError(describeError(err)),
    );
  }

  return (
    <div className={styles.list}>
      <div className={styles.head}>
        <h2 className={styles.title}>Your chats</h2>
        <button type="button" className="btn primary" onClick={onNew}>New chat</button>
      </div>
      {error && <p className={styles.error} role="alert">{error}</p>}
      {items === null && !error && <p className="muted" role="status">Loading your chats{"…"}</p>}
      {items?.length === 0 && <p className="muted">No saved chats yet. Ask the assistant something and it will show up here.</p>}
      <ul className={styles.items}>
        {items?.map((c) => (
          <li key={c.conversation_id} className={styles.item} data-current={c.conversation_id === currentId}>
            {renaming === c.conversation_id ? (
              <form className={styles.edit} onSubmit={(e) => saveTitle(e, c.conversation_id)}>
                <label htmlFor="chat-rename" className="sr-only">Chat name</label>
                <input id="chat-rename" className={styles.input} value={draft} maxLength={80} autoFocus onChange={(e) => setDraft(e.target.value)} />
                <button type="submit" className="btn primary" aria-disabled={!cleanTitle(draft)}>Save</button>
                <button type="button" className="btn" onClick={() => setRenaming(null)}>Cancel</button>
              </form>
            ) : (
              <>
                <button type="button" className={styles.open} onClick={() => onOpen(c.conversation_id)}>
                  <span className={styles.name}>{c.title}</span>
                  <span className={`${styles.when} muted`}>{relativeTime(c.updated_at)}</span>
                </button>
                {deleting === c.conversation_id ? (
                  <span className={styles.confirm} role="group" aria-label={`Delete ${c.title}?`}>
                    <span className="small">Delete this chat?</span>
                    <button type="button" className="btn" onClick={() => remove(c.conversation_id)}>Delete</button>
                    <button type="button" className="btn" onClick={() => setDeleting(null)}>Keep</button>
                  </span>
                ) : (
                  <span className={styles.actions}>
                    <button type="button" className="btn text" onClick={() => { setRenaming(c.conversation_id); setDraft(c.title); setDeleting(null); }}>Rename</button>
                    <button type="button" className="btn text" onClick={() => { setDeleting(c.conversation_id); setRenaming(null); }}>Delete</button>
                  </span>
                )}
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

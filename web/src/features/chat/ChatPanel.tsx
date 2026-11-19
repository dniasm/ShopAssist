import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useChat } from "./ChatContext";
import { TurnView } from "./TurnView";
import styles from "./ChatPanel.module.css";

const EXAMPLES_SIGNED_IN = ["Show me waterproof jackets under $100", "What's in my cart?", "Where is my latest order?"];
const EXAMPLES_SIGNED_OUT = ["Show me waterproof jackets under $100", "Any red hoodies?", "What are the best rated watches?"];
const PRODUCT_QUESTIONS = ["Is it in stock?", "Which sizes are left?", "Show me similar products"];

/** The assistant: a transcript that follows the conversation as it streams in, and a composer that always knows which page you are on. */
export function ChatPanel() {
  const { turns, signedIn, send, newChat, pageProduct, focusTick } = useChat();
  const [draft, setDraft] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  // take focus when the panel opens, and whenever something asks for it ("/", "Ask about this")
  useEffect(() => { input.current?.focus(); }, [focusTick]);

  // keep the newest line in view while it streams in, unless the shopper has scrolled up to read
  useLayoutEffect(() => {
    const el = log.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  });

  function submit(e?: FormEvent) {
    e?.preventDefault();
    if (!draft.trim()) return;
    stick.current = true;
    send(draft);
    setDraft("");
    input.current?.focus();
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
  }

  const examples = signedIn ? EXAMPLES_SIGNED_IN : EXAMPLES_SIGNED_OUT;
  const ask = (text: string) => { stick.current = true; send(text); };

  return (
    <div className={styles.panel}>
      <div className={styles.bar}>
        <button type="button" className="btn text" onClick={() => { stick.current = true; newChat(); input.current?.focus(); }} disabled={turns.length === 0}>New chat</button>
      </div>
      <div
        ref={log}
        className={styles.log}
        role="log"
        aria-label="Conversation with the assistant"
        aria-live="polite"
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}
      >
        {turns.length === 0 ? (
          <div className={styles.empty}>
            <p className={styles.emptyTitle}>Ask about products, stock, your cart or your orders</p>
            <p className="muted">
              {signedIn ? "I can look things up, add to your cart, and prepare a checkout or a cancellation for you to confirm." : "Ask about the catalogue. Sign in to ask about your cart and orders."}
            </p>
            <ul className={styles.examples}>
              {examples.map((x) => <li key={x}><button type="button" className={styles.example} onClick={() => ask(x)}>{x}</button></li>)}
            </ul>
          </div>
        ) : (
          <div className={styles.turns}>{turns.map((t) => <TurnView key={t.key} turn={t} />)}</div>
        )}
      </div>

      <form className={styles.composer} onSubmit={submit}>
        {pageProduct && (
          <div className={styles.context}>
            <span className={`${styles.chip} muted`}>Asking about: <strong>{pageProduct.name}</strong></span>
            {draft === "" && (
              <ul className={styles.quick}>
                {PRODUCT_QUESTIONS.map((q) => <li key={q}><button type="button" className={styles.example} onClick={() => ask(q)}>{q}</button></li>)}
              </ul>
            )}
          </div>
        )}
        <div className={styles.row}>
          <label htmlFor="chat-input" className="sr-only">Message the assistant</label>
          <textarea
            id="chat-input"
            ref={input}
            className={styles.input}
            rows={1}
            maxLength={500}
            value={draft}
            placeholder="Ask the assistant"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKey}
            enterKeyHint="send"
          />
          <button type="submit" className="btn primary" aria-disabled={!draft.trim()}>Send</button>
        </div>
      </form>
    </div>
  );
}

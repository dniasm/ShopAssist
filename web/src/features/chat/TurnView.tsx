import { useAuth } from "../auth/AuthContext";
import { ChatCardView } from "./ChatCards";
import { parseCards } from "./cards";
import { useChat } from "./ChatContext";
import { ProposalCard } from "./ProposalCard";
import { ToolTrace } from "./ToolTrace";
import { isWorking, type ChatSource, type ChatTurn } from "./transcript";
import styles from "./TurnView.module.css";

const SOURCE_WORDS: Record<ChatSource, string> = { llm: "AI", rules: "Rules", search: "Search" };

/** One exchange: the shopper's words, then the assistant's route (tools), its message with cards, anything it proposes, and how it ended. */
export function TurnView({ turn }: { turn: ChatTurn }) {
  const { retry } = useChat();
  const working = isWorking(turn);
  const answered = turn.messages.length > 0 || turn.proposals.length > 0;

  return (
    <article className={styles.turn} aria-busy={working}>
      {turn.request && <p className={styles.user}>{turn.request.text}</p>}

      <div className={styles.assistant}>
        <ToolTrace tools={turn.tools} working={working && !answered} />

        {turn.messages.map((m, i) => (
          <div key={i} className={styles.message}>
            {m.text && <p className={styles.text}>{m.text}</p>}
            {parseCards(m.cards).map((c, j) => <ChatCardView key={j} card={c} />)}
            {m.signIn && <SignInCard />}
          </div>
        ))}

        {turn.proposals.map((p) => <ProposalCard key={p.id} proposal={p} />)}

        {working && !turn.error && (
          <p className={styles.thinking} role="status">{turn.sending ? "Sending\u2026" : answered ? "Still working\u2026" : "Thinking\u2026"}</p>
        )}
        {turn.error && (
          <p className={styles.error} role="alert">
            {turn.error}
            {turn.request && <> <button type="button" className="btn text" onClick={() => retry(turn.key)}>Try again</button></>}
          </p>
        )}
        {turn.done && !turn.error && turn.source && <p className={styles.source} title={turn.source === "llm" ? "Answered by the language model" : turn.source === "rules" ? "Answered by the rules parser" : "Answered by a catalogue search"}>{SOURCE_WORDS[turn.source]}</p>}
      </div>
    </article>
  );
}

/** The signed-out visitor's card for anything about the cart or orders. */
function SignInCard() {
  const { openSignIn } = useAuth();
  return (
    <div className={styles.signIn}>
      <button type="button" className="btn primary" onClick={() => openSignIn()}>Sign in</button>
      <span className="muted small">No password: pick a customer to shop as.</span>
    </div>
  );
}

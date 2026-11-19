import { useId, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { useCart } from "../cart/CartContext";
import { useDrawer } from "../drawer/DrawerContext";
import styles from "./AddToCart.module.css";

interface Props {
  skuId: string | null;
  quantity: number;
  /** Why the button cannot be pressed right now ("Choose a size first", "Sold out"), or null when it can. */
  blockedReason: string | null;
  /** The cart refused because stock changed: refresh what the page believes about stock. */
  onStockChanged: () => void;
}

/** "Add to cart". Signed out: opens the sign-in picker first and then finishes the add. Signed in: PUTs the line, opens the
 *  cart drawer, or says plainly why not. It uses aria-disabled (not `disabled`) when blocked so keyboard and screen-reader
 *  users can still reach it and hear the reason. */
export function AddToCart({ skuId, quantity, blockedReason, onStockChanged }: Props) {
  const { user, openSignIn } = useAuth();
  const { addToCart } = useCart();
  const { show } = useDrawer();
  const tipId = useId();
  const [pending, setPending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function add() {
    if (!skuId) return;
    setPending(true);
    setProblem(null);
    const result = await addToCart(skuId, quantity);
    setPending(false);
    if (result.ok) {
      show("cart");
      return;
    }
    setProblem(result.message);
    if (result.outOfStock) onStockChanged();
  }

  function press() {
    if (blockedReason || pending) return;
    if (!user) {
      // the picker, then the add continues as that shopper
      openSignIn(() => { void add(); });
      return;
    }
    void add();
  }

  const blocked = blockedReason !== null || pending;
  return (
    <div className={styles.root}>
      <span className={styles.wrap}>
        <button
          type="button"
          className="btn primary large"
          aria-disabled={blocked}
          aria-describedby={blockedReason ? tipId : undefined}
          onClick={press}
        >
          {pending ? "Adding…" : "Add to cart"}
        </button>
        {blockedReason && <span role="tooltip" id={tipId} className={styles.tip}>{blockedReason}</span>}
      </span>
      {problem && <p className={styles.problem} role="alert">{problem}</p>}
    </div>
  );
}

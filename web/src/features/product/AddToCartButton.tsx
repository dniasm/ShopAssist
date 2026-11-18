import { useId } from "react";
import styles from "./AddToCartButton.module.css";

/** "Add to cart" until Phase 5.2 wires the cart. It uses aria-disabled rather than `disabled` so keyboard and screen-reader
 *  users can still reach it and hear why it is unavailable (a truly disabled button cannot take focus, so its tooltip
 *  would be mouse-only). The click does nothing. */
export function AddToCartButton() {
  const tipId = useId();
  return (
    <span className={styles.wrap}>
      <button type="button" className="btn primary large" aria-disabled="true" aria-describedby={tipId} onClick={(e) => e.preventDefault()}>
        Add to cart
      </button>
      <span role="tooltip" id={tipId} className={styles.tip}>Adding to the cart arrives in the next update.</span>
    </span>
  );
}

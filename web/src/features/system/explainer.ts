import { FORWARD_STEPS, STEP_LABELS, type ForwardStep } from "../checkout/saga";

export interface ExplainerStep {
  step: ForwardStep;
  /** The step's name, exactly as the live checkout page shows it. */
  label: string;
  does: string;
  ifFails: string;
}

const COPY: Record<ForwardStep, { does: string; ifFails: string }> = {
  VALIDATING: {
    does: "The order service asks the catalog for today's prices and checks the cart is not empty.",
    ifFails: "Nothing has been touched yet, so there is nothing to undo.",
  },
  RESERVING: {
    does: "The inventory service sets your items aside, all of them or none, so nobody else can buy the last one while you pay.",
    ifFails: "If anything is short, nothing is reserved and you are told which item sold out.",
  },
  CHARGING: {
    does: "The payment service charges the card you picked. The demo cards behave in fixed ways: works, declined, or coin flip.",
    ifFails: "The reservation is released, so the items go back on the shelf, and you can try another card.",
  },
  CONFIRMING: {
    does: "The stock leaves the shelf for good, the order becomes CONFIRMED and your cart is emptied.",
    ifFails: "The payment is refunded and the reservation released, in that order.",
  },
};

/** The checkout saga in plain words, in the order it runs, with what is undone when a step fails. */
export const CHECKOUT_STEPS: readonly ExplainerStep[] = FORWARD_STEPS.map((step) => ({ step, label: STEP_LABELS[step], ...COPY[step] }));

export const COMPENSATION_NOTE =
  "When a step fails, the steps before it are undone in reverse order. That undoing is a step of its own, \"Releasing your reserved items\", and you see it happen on the checkout page.";

export const SAFETY_NOTES: readonly string[] = [
  "Pressing Place order twice, or retrying after a bad connection, never creates two orders: the page sends the same key every time and the server answers with the same order.",
  "If the connection to the checkout is lost mid-way, the outcome is unknown. The order stays PENDING and you are only charged if it is confirmed; the system settles it within about two minutes.",
];

export const DELIVERY_NOTE =
  "After CONFIRMED a worker moves the order through PACKED, SHIPPED and OUT_FOR_DELIVERY to DELIVERED, one step every 8 seconds on this demo. You can cancel (with a refund) until it ships.";

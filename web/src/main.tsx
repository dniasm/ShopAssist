import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles/fonts.css";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/ui.css";

// The app restores scroll itself (listing position on Back, top on a new page); the browser must not second-guess it.
if ("scrollRestoration" in window.history) window.history.scrollRestoration = "manual";

const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

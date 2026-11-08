import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "./tokens.css";
import "./app.css";
import "./shell.css";
import "./intel.css";
import App from "./App";
import Login from "./Login";

// any 401 from the API means the session is missing or expired: show the sign-in screen
const _fetch = window.fetch.bind(window);
window.fetch = async (input, init) => {
  const r = await _fetch(input, init);
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  if (r.status === 401 && url.includes("/api/") && !url.includes("/api/v1/auth/login")) window.dispatchEvent(new Event("gt-auth-required"));
  return r;
};

const root = createRoot(document.getElementById("root")!);
let shown = false;
const showLogin = () => { if (!shown) { shown = true; root.render(<StrictMode><Login /></StrictMode>); } };
window.addEventListener("gt-auth-required", showLogin);
_fetch("/api/v1/auth/me").then((r) => {
  if (r.status === 401) showLogin();
  else root.render(<StrictMode><App /></StrictMode>);
}).catch(() => root.render(<StrictMode><App /></StrictMode>));

// Sign-in: the API requires an access token (docs/AUTH.md). The token is typed by the person, sent once to
// POST /api/v1/auth/login, and exchanged for an HttpOnly session cookie. Nothing is stored by this page.
import { useState } from "react";
import { KeyRound } from "lucide-react";

export default function Login() {
  const [tok, setTok] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tok.trim() || busy) return;
    setBusy(true); setErr("");
    try {
      const r = await fetch("/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: tok.trim() }) });
      if (r.ok) { location.reload(); return; }
      setErr(r.status === 429 ? "Too many attempts. Wait a few minutes." : "That token isn't valid.");
    } catch { setErr("Can't reach the server."); }
    setTok(""); setBusy(false);
  };
  return (
    <div className="gt-login" data-testid="login">
      <form className="gt-login-card" onSubmit={submit}>
        <div className="gt-login-icon"><KeyRound size={18} strokeWidth={1.5} /></div>
        <div style={{ fontSize: 17, fontWeight: 500 }}>Sign in to Groundtruth</div>
        <div className="gt-srcline" style={{ fontSize: 13 }}>Paste your access token. Ask the person who runs this server if you don't have one.</div>
        <input className="gt-input" type="password" autoFocus autoComplete="current-password" placeholder="Access token" aria-label="Access token"
          value={tok} onChange={(e) => setTok(e.target.value)} data-testid="login-token" />
        {err && <div className="gt-login-err" role="alert">{err}</div>}
        <button className="gt-pill gt-dark" type="submit" disabled={!tok.trim() || busy} style={{ alignSelf: "stretch" }}>Sign in</button>
      </form>
    </div>
  );
}

// Global navigation rail: a narrow, always-visible column. Every item does something real (no Monitor item: there is
// no scheduler or data refresh behind it yet, so it would be a dead button).
import { Bookmark, Compass, FileText, Home, MessagesSquare, Settings, ShieldCheck } from "lucide-react";

export type View = "investigations" | "reports" | "saved" | "validate";
type Props = {
  view: View; sidebarOpen: boolean; onHome: () => void; onView: (v: View) => void; onDiscover: () => void;
  onSettings: () => void; settingsOpen: boolean; validating: boolean; onValidate: () => void;
};
const I = { size: 18, strokeWidth: 1.5 } as const;

export default function NavRail(p: Props) {
  const item = (on: boolean, label: string, icon: JSX.Element, onClick: () => void, testid?: string) => (
    <button className={`gt-rail-btn${on ? " gt-rail-on" : ""}`} aria-label={label} aria-pressed={on} onClick={onClick}
      data-tip={label} data-testid={testid}>{icon}</button>);
  return (
    <nav className="gt-rail" aria-label="Main">
      <button className="gt-rail-logo" aria-label="Groundtruth home" data-tip="Groundtruth" onClick={p.onHome}>
        <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
          <rect x="1.5" y="1.5" width="19" height="19" rx="6" fill="#191919" />
          <path d="M6 13.5 9.5 10l2.5 2.5L16 8.5" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx="16" cy="8.5" r="1.6" fill="#EC3E8C" />
        </svg>
      </button>
      {item(false, "Home", <Home {...I} />, p.onHome, "rail-home")}
      {item(p.sidebarOpen && p.view === "investigations", "Investigations", <MessagesSquare {...I} />, () => p.onView("investigations"), "rail-investigations")}
      {item(false, "Discover", <Compass {...I} />, p.onDiscover, "rail-discover")}
      {item(p.sidebarOpen && p.view === "reports", "Reports", <FileText {...I} />, () => p.onView("reports"), "rail-reports")}
      {item(p.sidebarOpen && p.view === "saved", "Saved", <Bookmark {...I} />, () => p.onView("saved"), "rail-saved")}
      {item(p.validating, "Validate", <ShieldCheck {...I} />, p.onValidate, "rail-validate")}
      <span style={{ flex: 1 }} />
      {item(p.settingsOpen, "Settings", <Settings {...I} />, p.onSettings, "rail-settings")}
    </nav>
  );
}

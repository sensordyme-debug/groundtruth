// Inline icons, drawn as in the approved mockup (stroke 1.3–1.6, round caps).
import type { CSSProperties } from "react";

const s = { fill: "none", stroke: "currentColor", strokeLinecap: "round", strokeLinejoin: "round" } as const;

export const Plus = () => <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" {...s} strokeWidth="1.5" /></svg>;
export const Chevron = ({ style }: { style?: CSSProperties }) => <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" style={style}><path d="M4.5 2.5 8 6l-3.5 3.5" {...s} strokeWidth="1.5" /></svg>;
export const X = () => <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 2l6 6M8 2 2 8" {...s} strokeWidth="1.5" /></svg>;
export const More = () => <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="3.5" cy="8" r="1.2" fill="currentColor" /><circle cx="8" cy="8" r="1.2" fill="currentColor" /><circle cx="12.5" cy="8" r="1.2" fill="currentColor" /></svg>;
export const Up = () => <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M7 11.5v-9M3 6.5l4-4 4 4" {...s} strokeWidth="1.6" /></svg>;
export const Down = () => <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 3.5 5 6.5l3-3" {...s} strokeWidth="1.4" /></svg>;
export const Layers = () => <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.5 14 5.5 8 8.5 2 5.5z M2 8.5l6 3 6-3 M2 11l6 3 6-3" {...s} strokeWidth="1.3" /></svg>;
export const Plus14 = () => <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M7 2.5v9M2.5 7h9" {...s} strokeWidth="1.5" /></svg>;
export const Minus14 = () => <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M2.5 7h9" {...s} strokeWidth="1.5" /></svg>;
export const Close = () => <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 2.5l7 7M9.5 2.5l-7 7" {...s} strokeWidth="1.5" /></svg>;

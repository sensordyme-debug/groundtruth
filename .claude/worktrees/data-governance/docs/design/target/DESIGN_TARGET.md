# Groundtruth UI: design target (approved by Monish)

`Main.dc.html` in this folder is the approved interactive mockup. It is the spec for `web/app-v2`.
Open it in a browser to see it (it's a Design Component page; read its markup and the `<style>` block for exact values even if it doesn't render outside the canvas).

## Goal
1. Reproduce this mockup exactly: layout, flow, states, motion.
2. Then polish it to devin.ai-level finish ("pixel perfect" = Devin's quality bar, measured with Playwright on devin.ai). Inspired by, never copied: no Devin/Claude names, logos, images, text or code.

## What the mockup defines (match all of it)
- **Landing:** blurred aerial background + #f4f4f2 veil, "Groundtruth" wordmark top-left (15 px / 500), heading "What do you want to find?" (30 px / 400 / -0.02em), composer card 640 px wide, radius 20, hairline #e7e6e3, shadow `0 1px 2px rgba(0,0,0,.04), 0 12px 32px rgba(0,0,0,.06)`, 32 px round dark send button, max 2–3 suggestion pills (13 px, radius 999).
- **Transition:** landing fades out (.55 s), 400 px left panel slides in (.6 s, cubic-bezier(.2,.8,.2,1)), map fades in (.7 s), camera flies (1.6 s, cubic-bezier(.22,.8,.2,1)). Respect prefers-reduced-motion.
- **Chat panel:** user = right-aligned #ecebe8 pill radius 16; assistant = plain 15 px text; collapsed "Worked X s · N steps ›" line (13 px muted), expands to one-liners with ms; one result card (radius 14, hairline, title 15/500 + muted sub + 4 square thumbnails + ⋯ menu: Export CSV / GeoJSON / Details); follow-up suggestion pills under the latest reply only; typing dots while working; composer pinned bottom + footnote "NAIP 2022 · Mecklenburg County".
- **Pick in chat:** "Which do you mean?" with a 3×2 tile grid inside the message; click = pick (2 px #191919 border), hover ✕ = not this (tile fades to .35); "Continue" dark pill + "N picked" hint. Skipped when the query already says "houses".
- **Map chrome (frosted: rgba(255,255,255,.82) + blur 20 px):** year pill + layers button top-left (popover: Flood zones, Highways, 3D buildings); "Working" pill top-centre while busy; legend bottom-left (Pools dot, Docks bar after step 3); zoom bottom-right.
- **Results styling:** spotlight dim (imagery −22 %); step 1 = glowing accent dots; step 2+ = accent rings 30 px, 2.5 px border + dark halo; filters fade non-matches to .16 and pulse matches twice; docks in second accent with a dashed connector to their pool; selected ring turns white.
- **Detail sheet:** frosted 360 px panel slides from the right (.45 s), crop with a 2020↔2022 swipe + range slider, 2–3 plain-sentence facts, "Looks right" / "Not this" (feeds refine). Esc closes.
- **Tokens:** canvas #f4f4f2, surface #fff, ink #191919, muted #5c5c5c (body-size secondary text #6b6b68 for contrast), hairline #e7e6e3, user bubble #ecebe8, accent #EC3E8C (results), second accent #F5A524 (docks). Inter 400/500 only. Radii 8/10/14/16/20/999. 8-px grid. UI motion 180 ms ease-out.
- **Copy:** short and human. Never "envelope", "chips", "cells", "sense", "status", confidence bars or raw scores in the main view.

## Demo chain the build must perform
"houses with pools" → "in Lake Norman" → "with lake and dock access" → "which got docks since 2020". Also "swimming pools" → pick in chat → continue. Every number comes from real API envelopes (the mockup's numbers are placeholders).

## Differences from the mockup that are intended
- Real NAIP tiles in MapLibre instead of the drawn stand-in map.
- Real thumbnails (NAIP crops) in result cards and the detail sheet.
- Real data for counts, areas, distances.

## Process
1. Build in web/app-v2 (old UI stays at /classic).
2. Playwright: screenshot the mockup and your build at the same states (landing, mid-transition, each chain step, pick grid, detail sheet) at 1440×900 and 1920×1080 → docs/media/v2/compare/. List every visible difference and fix it.
3. Then the Devin polish pass: measure devin.ai's computed styles (fonts, spacing, radii, shadows, easing) per the STEP 0.5 instructions and tighten our tokens toward that finish without copying anything.
4. Stop for Monish's approval with the side-by-side screenshots.

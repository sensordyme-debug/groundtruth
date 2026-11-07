# Map actions

`agent/map_actions.schema.json` defines the agent's output actions. The web app applies them in order. Every action is idempotent: setting the same view, layer, or result twice has the same effect as setting it once. Actions reference tool envelopes as `step_N`; they never copy GeoJSON into the action stream.

- `fly_to`: move to a configured place bbox. `approximate: true` means the bbox is an unverified demo approximation.
- `show_candidates`: ask the user to choose an interpretation before searching for all matches.
- `add_layer`: add a context layer from an envelope.
- `add_results`: display a result envelope.
- `set_filter`: display a filtered envelope.
- `show_metrics`: display counts and areas from an envelope.
- `offer_download`: display a download from an export envelope.
- `say`: user-facing text. Any number in it must cite the step envelope that supplied it.

Unknown actions may be ignored by the web app. The current schema is a draft pending Monish's contract and web integration agreement.

# data/ (gitignored)

Everything here is local and regenerable from `scripts/`, except `labels/` — the human-made test set.

- `boundary/` Mecklenburg County boundary (Census TIGERweb)
- `naip/` NAIP coverage + scene lists (Planetary Computer STAC)
- `labels/` **human labels: journal `labels.jsonl` (source of truth), `labels.geojson` snapshot, `backups/`**
- `cache/` tile + geocode caches
- `layers/` OSM / FEMA / Overture extracts
- `chips/`, `emb/`, `index/` tiler output, embeddings, FAISS indices
- `out/` contact sheets and smoke-test output
- `logs/` server and job logs

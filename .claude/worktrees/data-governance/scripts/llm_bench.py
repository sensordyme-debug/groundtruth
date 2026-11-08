"""Local LLM latency/throughput through the PC → tunnel → laptop path.
  python scripts/llm_bench.py [--n 50]
Reply latency (50 real templated replies, no cache), first-token time (streaming), and generation tokens/s with 1 vs
2 concurrent requests (the server has 2 slots)."""
import argparse
import concurrent.futures as cf
import json
import sys
import time
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT))
from api.planner.llm import local  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=50)
a = ap.parse_args()
TEMPL = ["Found {n} houses with pools around Lake Norman.", "{n} lakefront houses with a pool and their own dock.",
         "{n} pools in Ballantyne within 1000 m of I-485; 1.94 acres in total; median 531 m to I-485.",
         "121 buildings taller than 30 m in Uptown; 98.7 acres in total.", "{n} boat docks in Lake Norman; 37.4 acres in total."]
reply_ms, guard = [], 0
for k in range(a.n):
    t = TEMPL[k % len(TEMPL)].format(n=100 + 7 * k)
    local._REPLY_CACHE.clear()
    out, info = local.llm_reply(t, [])
    reply_ms.append(info["ms"])
    guard += out == t
# first-token time (streaming)
ttft = []
for k in range(10):
    t0 = time.perf_counter()
    with httpx.Client(timeout=30) as c, c.stream("POST", f"{local.URL}/chat/completions", json={
            "messages": [{"role": "system", "content": local._system()}, {"role": "user", "content": f"REQUEST: pools in Ballantyne #{k}"}],
            "temperature": 0, "max_tokens": 64, "stream": True, "cache_prompt": True}) as r:
        for line in r.iter_lines():
            if line.startswith("data: ") and '"content"' in line and '"content":""' not in line.replace(" ", ""):
                ttft.append((time.perf_counter() - t0) * 1000)
                break


def gen(i):
    t0 = time.perf_counter()
    r = httpx.post(f"{local.URL}/chat/completions", timeout=120, json={
        "messages": [{"role": "user", "content": f"Write 120 words about aerial imagery #{i}."}],
        "temperature": 0.3, "max_tokens": 128, "cache_prompt": False}).json()
    return r["usage"]["completion_tokens"], time.perf_counter() - t0, r.get("timings", {})


tput = {}
for conc in (1, 2):
    with cf.ThreadPoolExecutor(conc) as ex:
        t0 = time.perf_counter()
        res = list(ex.map(gen, range(conc * 2)))
        wall = time.perf_counter() - t0
    toks = sum(r[0] for r in res)
    tput[conc] = {"aggregate_tok_s": round(toks / wall, 1),
                  "per_request_gen_tok_s": round(float(np.mean([r[2].get("predicted_per_second", 0) for r in res])), 1),
                  "prompt_tok_s": round(float(np.mean([r[2].get("prompt_per_second", 0) for r in res])), 1)}
out = {"reply_ms_p50": round(float(np.percentile(reply_ms, 50))), "reply_ms_p95": round(float(np.percentile(reply_ms, 95))),
       "reply_template_fallbacks": guard, "replies": a.n, "first_token_ms_p50": round(float(np.percentile(ttft, 50))),
       "first_token_ms_p95": round(float(np.percentile(ttft, 95))), "throughput": tput}
print(json.dumps(out, indent=1))
(ROOT / "data" / "out" / "llm_bench.json").write_text(json.dumps(out, indent=1))

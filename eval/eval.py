"""
Run semantic search eval: python eval/eval.py
Requires API running at localhost:8000 with demo data seeded.
"""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).parent
data = json.loads((ROOT/"golden.json").read_text())
print(f"Loaded {len(data['items'])} items, {len(data['queries'])} queries")
print("TODO: Seed items via POST /api/v1/ingest then query GET /api/v1/search?q=...")
print("Metric: Recall@5, MRR — target 0.85")
# Stub: with real API, loop queries and check ranking
for q in data["queries"]:
    print(f"  query: {q['q']!r} -> expect {q['expect']}")
print("Eval harness ready — extend to call live API.")

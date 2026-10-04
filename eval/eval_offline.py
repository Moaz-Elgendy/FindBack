import json
import re
from pathlib import Path


def tokens(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def rank(items, query):
    q = tokens(query)
    return sorted(items, key=lambda item: len(q & tokens(" ".join([item["title"], item["summary"], *item["tags"]]))), reverse=True)


def main():
    data = json.loads((Path(__file__).parent / "golden.json").read_text())
    reciprocal, hits = [], 0
    for case in data["queries"]:
        ids = [x["id"] for x in rank(data["items"], case["q"])]
        position = ids.index(case["expect"]) + 1
        hits += position <= 5
        reciprocal.append(1 / position)
    recall = hits / len(data["queries"])
    mrr = sum(reciprocal) / len(reciprocal)
    print(f"Recall@5={recall:.3f} MRR={mrr:.3f} queries={len(reciprocal)}")
    raise SystemExit(0 if recall >= 0.85 else 1)


if __name__ == "__main__":
    main()

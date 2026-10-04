# Phase 19 — Search and AI evaluation report

Reporting only. Nothing in this phase changed product behaviour, and the suite
is deliberately not wired into CI as a pass/fail gate: a red build would invite
tuning the product to the dataset, which is the opposite of what an evaluation
is for.

## How to run

```
cd backend
TEST_DATABASE_URL=postgresql://findback:findback@localhost:55432/findback \
  .venv/bin/python -m pytest tests/test_phase19_evaluation.py -q -s
```

The live measurement is skipped when `TEST_DATABASE_URL` is unset; the metric
and corpus tests still run, so the suite is useful without a database.

## What was measured

A 13-document corpus across the eight required buckets — video, article, post,
recipe, product, tutorial, AI tool, mixed-language — with near-decoys on
purpose (two recipes, two products, two tutorials), so returning the right
*type* of content is not enough to score well.

18 queries, all imperfect. The five the phase names are in verbatim:

| query | difficulty | gold |
|---|---|---|
| that video about Claude skills | vague reference | claude_skills_video |
| the mushroom chicken recipe | misremembered detail | chicken_cream_recipe |
| AI tool for making presentations | attribute only | deck_tool |
| that AWS deployment recovery thing | vague reference | aws_deploy_talk |
| the headphones I was looking at | vague reference | headphones_product |

Definitions used, so the numbers are unambiguous:

- **Recall@k** — share of queries whose gold document is in the top *k*.
  A query with several gold documents counts as a hit if **any** appears.
- **MRR** — mean of 1/rank, 1-based; a missing gold contributes 0.
- **Grounded** — share of a brief's checkable statements supported by its
  source: verbatim, or ≥60% of its content words present (light stemming,
  stopwords removed). Verbatim alone would score faithful paraphrase as
  hallucination, which would make the metric measure copying, not accuracy.
- **Usefulness** — named checks (overview, title, highlights, topics,
  declared content type, video timestamps, and every key the profile owns).
- **Extraction correctness** — right profile *and* every profile key present.
- **Explanation correctness** — every term the match reason claims really is
  in the indexed text.
## Results

Run of 2026-03-10, `provider: oracle` — see the caveat below.

```
Retrieval
  Recall@1              0.722
  Recall@5              0.944
  MRR                   0.819
  queries               18

Brief groundedness      1.000  (141 statements checked)
Brief usefulness        1.000
Extraction correct      8/13
Explanations valid      32/35
Semantic-only matches   145
```

**Read the provider line first.** No AI key was configured, so `UNDERSTAND` and
`EMBED` were answered by a deterministic oracle: an IDF-weighted bag of tokens
for embeddings, and the gold brief for generation. That means these numbers
describe the **retrieval machinery**, not model quality. The brief metrics come
back at 1.000 largely because the oracle returns gold briefs. A provider-backed
run is the only thing that can speak to factuality, usefulness or extraction
quality, and none has been done yet.

## Findings

### 1. Extraction correctness is 8/13, and the cause is identifiable

Five mismatches, and every one is the classifier, not the extractor:

```
aws_deploy_talk        WRONG list     -> product  (keys present)
headphones_product     WRONG product  -> general  (keys present)
keyboard_product       WRONG product  -> general  (keys present)
k8s_tutorial           WRONG tutorial -> general  (keys present)
pg_replication_tutorial WRONG tutorial -> general (keys present)
```

`app/services/profiles.py:140` picks a profile from the URL and a short text
window:

```python
if "youtube.com" in lowered_url or "youtu.be" in lowered_url:
    return LIST
```

The corpus uses `https://eval.findback.test/<key>`, which carries none of the
domain or marker signals `_SIGNALS` looks for, so almost everything falls
through to `GENERAL`. `extractor.py:165` then hard-overwrites whatever the
### 2. One query never retrieves its gold document

`静的サイトのビルドが速いツール` — the Japanese query for the Japanese post —
returns the gold document in neither the top 1 nor the top 10. Every other
query finds its gold within Recall@5.

The CJK corpus is short, and both the query and the document are dense Japanese
with no Latin overlap, so the IDF oracle has very little to work with. With a
real multilingual embedding model this would likely resolve, so this is
reported as "unverified against a real provider", not as a confirmed defect.

### 3. Three match reasons name a term that is not in the indexed text

```
'AI tool for making presentations' -> translation_tool: 'ai + tool (in the content)'
'postgres replication between two servers' -> headphones_product: 'two (in the content)'
'rolled back the deploy and felt fine about it' -> chicken_cream_recipe: 'back (in the content)'
```

`evidence_reason` (`search.py:105`) falls through to the body and reports the
matched terms with a position. The claim is technically true — the terms *are*
in the matched chunk — but the brief-level `search_text` used by the metric
does not contain them, so the explanation points at evidence a reader cannot
see in the result. "two" and "back" are also stopword-ish fragments, which
makes for a poor explanation even where it is accurate.

32 of 35 explanations are fully valid. The other 145 results report
`similar meaning`, which is `evidence_reason`'s explicit and honest fallback
when there is no lexical evidence at all — counted separately rather than as a
failure, because flagging correct behaviour as a defect would make the metric
useless.

### 4. Retrieval is strong on vague references, weaker on paraphrase

The five named queries mostly land first. The four that came back but not
first:

```
that AWS deployment recovery thing
that kubernetes recovery tutorial
rollback when a deploy breaks
sous vide
```

The last is an unanswerable query (nothing in the corpus is sous vide) that was
included deliberately; the system returns *something* rather than nothing.
Whether that is right is a product decision, not a metric question.

Pattern worth noting: both recovery queries rank the AWS talk second when the
Kubernetes tutorial is gold, and vice versa. Both documents are about
recovering infrastructure, and the corpus is small enough that this is close to
the decoy limit rather than evidence of a fusion problem.

## Limitations

- **No provider was configured.** The retrieval numbers describe the ranking
  machinery under a synthetic embedding model. The brief numbers describe the
  pipeline's handling of a brief, not a model's ability to write one. Nothing
  here should be quoted as a model quality score.
- **13 documents and 18 queries.** Enough to catch structural problems, far
  too few for a stable Recall@1 estimate; a single query moves it by 5.6
  points.
- **Factuality has no judge model.** Groundedness is lexical support, so a
  faithful-but-reworded statement passes and a paraphrase that inverts meaning
  could pass. It catches fabricated facts, not subtle distortion.
- **One user, one corpus.** No cross-user leakage, no visibility effects, and
  no ranking-under-load behaviour is exercised.
- `groundedness` treats a `content_type` value as valid if it is one of the
  five known profiles. A brief could call a video a "recipe" and still be
  scored grounded on that field; classification correctness is a separate
  metric, which is why finding 1 exists.
model decided:

```python
structured["content_type"] = profile.name
```

`aws_deploy_talk → product` is the clearest symptom: the talk text contains
"class", which matches the product signal.

**This is partly a fixture artefact** — real URLs would classify far better. But
it also exposes something real: `structured_data.content_type` reports the
*profile that was chosen*, never the content type the model inferred. When the
two disagree, the model's better answer is discarded and no trace is kept.
Severity: medium; not fixed here, per the phase rule.
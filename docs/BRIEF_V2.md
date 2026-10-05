# Brief v2 operations and verification

New saves keep using the existing Celery/Redis outbox. The request stores the link
and returns; metadata, downloads, transcription, OCR, LLM extraction and embeddings
run in the worker. Existing items and the legacy `summary`/string `key_points` API
fields remain readable. No bulk reprocessing of old rows occurs during migration.

## Storage and rollout

Migration `0012_brief_v2` adds four fields to `items`: `evidence_bundle`, `brief_v2`,
`processing_metadata` (JSONB with empty-object defaults), and `needs_retry` (false).
A partial index supports full-transcript cache lookups by platform/source id.
Migration downgrade removes only these new fields/index. Legacy columns remain.

Run `cd backend && python -m alembic upgrade head` against the intended database
before starting the updated API/workers. Rebuild the existing Docker services to
install ffmpeg, Tesseract English/Arabic data, yt-dlp and faster-whisper. Saving,
queue publishing, user ownership, and privacy-scoped sharing keep their existing
structure. Transcript evidence remains in JSONB after raw-page retention runs;
downloaded video, WAV audio and sampled images exist only in temporary directories.

The Flutter SQLite mirror upgrades from version 1 to 2 by adding `brief_payload`.
It retains existing saves and caches new Brief fields for offline detail viewing.
The API exposes both `key_points` (legacy strings) and `key_points_with_refs`
(objects with `point` and `source_ref`), plus Instant Brief, takeaway, confidence,
missing-information badge, topics, phrases, evidence, version and retry state.
Fetch errors stay internal and are excluded from model input and item responses.

## Runtime requirements

Outside Docker install `ffmpeg`, `tesseract-ocr`, `tesseract-ocr-eng` and
`tesseract-ocr-ara`, then install `backend/requirements.txt`. The Dockerfile installs
these system packages. Local STT uses multilingual faster-whisper `base`, CPU int8
by default. Its first run downloads public model weights; provision them in the
worker model cache beforehand for installations without model-download access.
Do not select an English-only `.en` model. Arabic and English are auto-detected.

yt-dlp is pinned in one line in `backend/requirements.txt`; update the pin and
rebuild workers when platform extraction changes. Cookies are optional configuration
and disabled by default. No CAPTCHA solving or account automation is used.

## Environment settings

| Variable | Default | Purpose |
|---|---|---|
| `MEDIA_ENABLED` | `true` | Enable worker acquisition; false keeps metadata evidence |
| `MEDIA_MAX_DURATION_SECONDS` | `1200` | Local STT/short-video ceiling |
| `MEDIA_LONG_MAX_SECONDS` | `7200` | Hard long-video acquisition ceiling; unknown durations are flagged |
| `MEDIA_MAX_FILESIZE_MB` | `150` | Total downloaded media limit; 480p uses the short edge for portrait media |
| `MEDIA_FRAME_CAP` | `10` | Scene frames, hard maximum 12 |
| `MEDIA_FFMPEG_TIMEOUT` | `300` | Audio/frame command timeout, seconds |
| `MEDIA_OCR_LANGUAGES` | `eng+ara` | Installed Tesseract language packs |
| `MEDIA_COOKIES_PATH` | unset | Optional cookies file, never hardcoded |
| `MEDIA_RETRY_MAX_ATTEMPTS` | `3` | Total evidence attempts, including the initial attempt |
| `MEDIA_RETRY_BASE_SECONDS` | `30` | Exponential retry delay, capped at one hour |
| `STT_PROVIDER` | `local` | Short-clip transcription provider |
| `STT_MODEL` | `base` | Multilingual faster-whisper model |
| `STT_DEVICE` | `cpu` | Local inference device |
| `STT_COMPUTE_TYPE` | `int8` | Local inference precision |
| `STT_LONG_PROVIDER` | `cloud` | Long-video provider |
| `STT_CLOUD_BASE_URL` | unset | OpenAI-compatible STT API base ending in `/v1` |
| `STT_CLOUD_API_KEY` | unset | Secret cloud credential |
| `STT_CLOUD_MODEL` | `whisper-1` | Cloud model supporting verbose timestamped JSON |
| `STT_CLOUD_TIMEOUT` | `180` | Timeout per cloud upload, seconds |
| `STT_CLOUD_COST_PER_MINUTE` | unset | Operator-provided pricing; absent means cost unknown |
| `BRIEF_NORMAL_MODEL` | existing AI model | Normal/map extraction model override |
| `BRIEF_COMPLEX_MODEL` | existing AI model | Merge/complex extraction model override |
| `BRIEF_MAX_TOKENS` | `2400` | Output budget, minimum 1200 |
| `BRIEF_CHUNK_CHARS` | `12000` | Transcript map budget; request ceiling adds 12,000 characters for metadata/merge overhead |
| `BRIEF_INPUT_COST_PER_MILLION` | unset | Input token pricing; absent means cost unknown |
| `BRIEF_OUTPUT_COST_PER_MILLION` | unset | Output token pricing; absent means cost unknown |

Cloud providers can be replaced using `transcription.register_provider`. Text
providers continue through the existing AI gateway. Long audio uploads are split
into ten-minute WAV files and their returned timestamps are offset into the original
video timeline. No cloud configuration means long-video STT fails gracefully and the
caption/OCR Brief remains available. Select normal/complex models appropriate to the
existing provider and configure pricing only when known.

## Retry and cache behavior

A video without a non-trivial transcript gets a grounded lower-confidence Brief,
a separate missing-information badge, and `needs_retry=true` while another attempt
is due. The existing durable job is reset to FETCH with exponential backoff. The
current Brief remains `ready` and searchable. A successful transcript replaces the
weaker Brief, rebuilds chunks and re-embeds. After the configured total-attempt cap,
`needs_retry=false`; the remaining missing-information badge stays visible.

Evidence is cached by platform/source id using existing item rows. Unknown/private
content is reusable only by its owner; PUBLIC assets can share evidence under the
existing privacy rules. No user notes or user intent enter the evidence, prompt,
shared Brief or embeddings. Legacy artifacts cannot bypass v2 extraction on a new
processing job.

## Evaluation

From the repository root, with backend dependencies installed:

```bash
python scripts/eval_brief.py
python scripts/eval_brief.py --live
python scripts/eval_brief.py --url 'https://www.facebook.com/share/r/19PXq2Y3AR/' \
  --title '5 claude code skills every developer should know' --output /tmp/facebook-brief.json
```

Default evaluation uses six supplied evidence/LLM fixtures (caption, transcript,
OCR, Arabic, recipe, article), runs the real understanding/Brief/chunk stages, and
prints JSON plus forbidden-phrase, tag-count, timestamp and confidence checks. It
never downloads media or calls a provider. `--live` evaluates fixtures using the
configured LLM. `--url` performs real acquisition and compares caption-only with
full acquisition; it may call transcription/model services and use configured
cloud credentials. Output errors are operator diagnostics, not item UI content.
Fixture output passing is regression proof, not a claim about live model quality.

```bash
TEST_DATABASE_URL=postgresql://findback:findback@localhost:5432/fb_brief_upgrade \
  backend/.venv/bin/python -m pytest backend/tests -q
(cd mobile && flutter test && flutter analyze)
```

The repository guard requires a dedicated `fb_*` or test-named database. Database
integration tests create/drop throwaway databases, so the test role needs that
permission. Downloads, Whisper and the LLM are mocked in backend tests. Tests also
exercise real PostgreSQL migration/worker/retry/privacy behavior and SQLite upgrade.

## Limits

Private/login-gated media, unavailable formats or upstream outages retain a weaker
Brief after bounded retries. No timestamp or actual skill name is invented. When
all AI attempts fail, the pipeline keeps an explicitly marked grounded offline
fallback. Very sparse evidence may support fewer than 15 tags and fewer than five
search phrases; fallback metadata records this rather than fabricating search terms.
Accepted model responses must satisfy the strict 15–30 tags / 5–8 phrases schema.
Long transcripts use pairwise recursive merges, with batch-specific timestamps.
Oversized individual segments are split without inventing new timestamps. Requests
exceeding the configured chunk budget plus 12,000 characters use the grounded
fallback rather than submitting an unbounded prompt.

OCR depends on the installed language packs and legible scene frames; a capped
sample can miss on-screen text. Existing YouTube captions skip audio/STT, while
frame processing still runs. Audio-only fallback cannot supply frames. Search uses
existing semantic/full-text fusion plus bounded stdlib tag-spelling expansion;
Arabic terms are retained, without adding a new search service or DB extension.

# FindBack in one sentence
A personal memory engine: the user saves content, FindBack understands it,
and the user can find it later from imperfect memory.

# Flow
Share -> Save immediately -> Process in background -> Extract useful info
-> Structured Brief -> Store for search -> Retrieve later.

# Core data model (target)
- ContentAsset = the content itself (shared, reusable when public).
- UserMemory = one user's relationship to that content (always private):
  notes, intent, timestamps, save count.
One ContentAsset can have many UserMemory rows.
Item = URL = user save is NOT the final design.

# Product rules
1. Saving must be fast. Never wait for AI during save.
2. Safe public content is processed once and reused.
3. A duplicate URL does NOT mean the content is safe to share between users.
   Visibility: PUBLIC (may be shared) | PRIVATE (never shared) | UNKNOWN (treat as PRIVATE).
4. The Brief extracts useful value. It is not a generic summary.
   Example: "5 Claude skills" video -> list of the 5 skills + timestamp, not 3 bullets.
5. Search is by meaning. Users do not remember titles.
6. User notes, intent, history, timestamps are always private.
7. Do not couple the app to one AI provider.

# Out of scope (never build unless a phase says so)
Custom/fine-tuned LLMs, Kafka, RabbitMQ, Kubernetes workers, microservices,
recommendation engine, social/public sharing, collaboration, browser extension,
reminders, chatbot, autonomous AI actions, price monitoring, mass crawling,
perceptual video hashing, on-device LLM pipeline.
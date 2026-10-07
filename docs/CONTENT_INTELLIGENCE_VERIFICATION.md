# Content intelligence verification — Phase 4

Verified on 2026-10-07.

## Behavior

- Home topic chips use topics actually stored in saved Briefs. Existing content-type categories are no longer the quick-filter panel.
- The Filters sheet supports topic, content type, entity, intent, action, source domain, and saved UTC date. Values come from saved metadata.
- The metadata inventory walks every existing library page, including items beyond the first 20. It warms the existing offline cache.
- Selected dimensions combine with AND. Library filtering happens before cursor pagination. Search filtering applies to all five candidate paths before their limits and ranking.
- Existing category API parameters remain supported. No schema migration, worker change, AI-provider change, or new endpoint was introduced.
- Existing `/api/v1/items` and `/api/v1/search` accept an optional `intelligence` JSON query parameter. Invalid keys, values and dates return 422. Search results include the metadata needed for the same offline/UI behavior.
- Offline metadata persists in the existing `brief_payload`; SQLite filtering precedes its row limit.
- Clearing search reloads the library with the current filters. A failed connectivity probe uses the cache and clears the loading state.

## Verification

- Flutter full suite: **104 passed, 1 skipped**. `flutter analyze`: **No issues found**.
- Focused backend intelligence + existing hybrid search tests: **29 passed**.
- Backend full suite: **779 passed, 3 failed, 1 skipped**. All three failures are in the unchanged `tests/test_android_launcher.py`: `test_launcher_forwards_api_before_starting_flutter`, `test_launcher_does_not_start_flutter_when_route_fails[api]`, and `test_launcher_uses_explicit_device_when_multiple_are_connected`. They report `Could not identify the connected phone`.
- Backend tested in an isolated PostgreSQL/pgvector container on the existing Compose network. Production PostgreSQL could not start: Docker returned a host-port exposure 500 for 55433. No production database content was queried or changed in this phase.
- Tests exercise seven combined dimensions, exact entity matching, invalid filters, HTTP responses from both endpoints, 60 search distractors, a topic beyond library page 20, combined dropdown filtering, clear/search transitions, and offline filtering before limits.
- Debug APK built successfully. Device installation/live visual verification: **NOT RUN**, because `adb devices -l` lists no connected device.

Commands (Flutter from `mobile/`):

```sh
flutter test --reporter expanded --concurrency=1
flutter analyze
GRADLE_OPTS='-Dorg.gradle.jvmargs=-Xmx1024m -Dorg.gradle.workers.max=2 -Dorg.gradle.daemon=false' flutter build apk --debug
```

Backend test container commands:

```sh
docker run --rm --network findback_default -v /home/moaz/FindBack:/workspace -w /workspace/backend -e TEST_DATABASE_URL=postgresql://findback:findback@findback-phase4-test-postgres:5432/findback_test findback-api python -m pytest tests/test_intelligence_filters.py tests/test_phase12_hybrid.py -q
docker run --rm --network findback_default -v /usr/bin/docker:/usr/bin/docker:ro -v /usr/local/lib/docker/cli-plugins/docker-compose:/usr/local/lib/docker/cli-plugins/docker-compose:ro -v /home/moaz/FindBack:/workspace -w /workspace/backend -e TEST_DATABASE_URL=postgresql://findback:findback@findback-phase4-test-postgres:5432/findback_test findback-api python -m pytest -q
```

## Limits

Importance and relationships are not currently stored or defined; this phase does not invent them. Metadata choices use a complete library scan, and search uses a UUID allowlist across candidate queries. Large-library performance has not been benchmarked; server facets and SQL subqueries are the next options if measured performance requires them. Entries without a metadata value do not match a selected value for that dimension. No live provider/pipeline evaluation was needed or run for these filters.

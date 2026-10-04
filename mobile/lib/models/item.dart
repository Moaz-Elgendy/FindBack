import 'json_utils.dart';

/// `GET /api/v1/items` and `GET /api/v1/items/{id}` (backend `ItemDetail`).
class ItemDetail {
  const ItemDetail({
    required this.id,
    required this.url,
    required this.canonicalUrl,
    required this.keyPoints,
    required this.entities,
    required this.tags,
    this.title,
    this.titleClean,
    this.summary,
    this.category,
    this.thumbnailUrl,
    this.sourceDomain,
    this.sourceType,
    this.status = 'pending',
    this.createdAt,
    this.processedAt,
  });

  factory ItemDetail.fromJson(Map<String, dynamic> json) {
    final url = json['url'] as String? ?? '';
    return ItemDetail(
      id: asId(json['id']),
      url: url,
      canonicalUrl: json['canonical_url'] as String? ?? url,
      keyPoints: stringList(json['key_points']),
      entities: objectMap(json['entities']),
      tags: stringList(json['tags']),
      title: json['title'] as String?,
      titleClean: json['title_clean'] as String?,
      summary: json['summary'] as String?,
      category: json['category'] as String?,
      thumbnailUrl: json['thumbnail_url'] as String?,
      sourceDomain: json['source_domain'] as String?,
      sourceType: json['source_type'] as String?,
      status: json['status'] as String? ?? 'pending',
      createdAt: parseDate(json['created_at']),
      processedAt: parseDate(json['processed_at']),
    );
  }

  final String id;
  final String url;
  final String canonicalUrl;
  final List<String> keyPoints;
  final Map<String, Object?> entities;
  final List<String> tags;
  final String? title;
  final String? titleClean;
  final String? summary;
  final String? category;
  final String? thumbnailUrl;
  final String? sourceDomain;
  final String? sourceType;
  final String status;
  final DateTime? createdAt;
  final DateTime? processedAt;

  bool get isReady => status == 'ready';

  /// The server gave up on processing this save (its job is parked FAILED after
  /// `JOB_MAX_ATTEMPTS`). Kept distinct from "still working": the first needs a
  /// retry, the second only needs time, and the app used to show both as a
  /// permanent "still being processed" banner.
  bool get isFailed => status == 'failed';

  /// Cleaned title first, then the raw one, then the URL as a last resort.
  String get bestTitle {
    if (titleClean != null && titleClean!.isNotEmpty) return titleClean!;
    if (title != null && title!.isNotEmpty) return title!;
    return url;
  }

  List<String> get ingredients => stringList(entities['ingredients']);

  /// Shape used by the local cache mirror (`items` table in SQLite).
  Map<String, Object?> toLocalRow() => <String, Object?>{
        'id': id,
        'url': url,
        'canonical_url': canonicalUrl,
        'title': title,
        'title_clean': titleClean,
        'summary': summary,
        'category': category ?? 'other',
        'tags': encodeTags(tags),
        'source_domain': sourceDomain,
        'thumbnail_url': thumbnailUrl,
        'status': status,
        'created_at': createdAt?.toIso8601String(),
      };

  /// Reverse of [toLocalRow] — the offline copy of an item we already cached.
  factory ItemDetail.fromLocalRow(Map<String, Object?> row) {
    final url = row['url'] as String? ?? '';
    return ItemDetail(
      id: row['id']?.toString() ?? '',
      url: url,
      canonicalUrl: row['canonical_url'] as String? ?? url,
      // The mirror keeps no enrichment, so an offline detail view shows the
      // preview text as the summary and no key points.
      keyPoints: const <String>[],
      entities: const <String, Object?>{},
      tags: decodeTags(row['tags']),
      title: row['title'] as String?,
      titleClean: row['title_clean'] as String?,
      summary: row['summary'] as String?,
      category: row['category'] as String?,
      thumbnailUrl: row['thumbnail_url'] as String?,
      sourceDomain: row['source_domain'] as String?,
      status: row['status'] as String? ?? 'pending',
      createdAt: parseDate(row['created_at']),
    );
  }
}

/// A page of recent items; `nextCursor` is opaque and passed straight back.
class ItemPage {
  const ItemPage({required this.items, this.nextCursor});

  factory ItemPage.fromJson(Map<String, dynamic> json) => ItemPage(
        items: asObjectList(json['items']).map(ItemDetail.fromJson).toList(growable: false),
        nextCursor: json['next_cursor'] as String?,
      );

  final List<ItemDetail> items;
  final String? nextCursor;
}

class IngestResult {
  const IngestResult({required this.id, required this.status, required this.canonicalUrl});

  factory IngestResult.fromJson(Map<String, dynamic> json) => IngestResult(
        id: asId(json['id']),
        status: json['status'] as String? ?? 'pending',
        canonicalUrl: json['canonical_url'] as String? ?? '',
      );

  final String id;
  final String status;
  final String canonicalUrl;
}

/// One queued capture; mirrors the backend `SyncItem` schema.
class SyncItem {
  const SyncItem({
    required this.clientId,
    required this.url,
    required this.capturedAt,
    this.preview,
    this.titleHint,
  });

  factory SyncItem.fromRow(Map<String, Object?> row) => SyncItem(
        clientId: row['client_id']?.toString() ?? '',
        url: row['url'] as String? ?? '',
        capturedAt: row['captured_at'] as String? ?? '',
        preview: row['preview'] as String?,
        titleHint: row['title_hint'] as String?,
      );

  final String clientId;
  final String url;
  final String capturedAt;
  final String? preview;
  final String? titleHint;

  Map<String, Object?> toJson() => <String, Object?>{
        'client_id': clientId,
        'url': url,
        'captured_at': capturedAt,
        'preview': preview,
        'title_hint': titleHint,
      };
}

/// A queued capture the server accepted, pairing the device-generated
/// `client_id` with the id the API assigned.
class MappedSave {
  const MappedSave({required this.clientId, required this.serverId});

  final String clientId;
  final String serverId;
}

/// `POST /api/v1/sync/batch`: which client ids landed and which did not.
class SyncBatchResult {
  const SyncBatchResult({required this.mapped, required this.failedClientIds});

  factory SyncBatchResult.fromJson(Map<String, dynamic> json) {
    final mapped = <MappedSave>[];
    for (final Map<String, dynamic> row in asObjectList(json['mapped'])) {
      final clientId = row['client_id'];
      final id = row['id'];
      if (clientId != null && id != null) {
        mapped.add(MappedSave(clientId: clientId.toString(), serverId: id.toString()));
      }
    }
    final failed = <String>[];
    for (final Map<String, dynamic> row in asObjectList(json['errors'])) {
      final clientId = row['client_id'];
      if (clientId != null) failed.add(clientId.toString());
    }
    return SyncBatchResult(mapped: mapped, failedClientIds: failed);
  }

  final List<MappedSave> mapped;
  final List<String> failedClientIds;

  bool get isEmpty => mapped.isEmpty && failedClientIds.isEmpty;
}

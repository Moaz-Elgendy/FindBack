/// Shared, null-tolerant JSON coercion used by every wire model.
///
/// `library;` must precede the imports, otherwise the compiler rejects it with
/// "the library directive must appear before all other directives".
library;

import 'dart:convert';

/// The local SQLite cache stores tags as a JSON string (SQLite has no array).
String encodeTags(List<String> tags) => jsonEncode(tags);

/// Tags are a JSON-encoded string in the SQLite mirror and a real array on the
/// wire; both shapes reach the UI through here.
List<String> decodeTags(Object? value) {
  if (value is List) return value.whereType<String>().toList(growable: false);
  if (value is String && value.trim().isNotEmpty) {
    try {
      return stringList(jsonDecode(value));
    } on FormatException {
      return const [];
    }
  }
  return const [];
}

List<String> stringList(Object? value) {
  if (value is List) return value.whereType<String>().toList(growable: false);
  return const [];
}

Map<String, Object?> objectMap(Object? value) {
  if (value is Map) {
    return value.map((key, dynamic v) => MapEntry(key.toString(), v));
  }
  return const {};
}

DateTime? parseDate(Object? value) {
  if (value is String && value.isNotEmpty) return DateTime.tryParse(value);
  return null;
}

Map<String, dynamic> asObject(Object? value) => Map<String, dynamic>.from(value as Map);

List<Map<String, dynamic>> asObjectList(Object? value) =>
    (value as List? ?? const []).map(asObject).toList(growable: false);

/// Ids arrive as UUID strings, but never trust the wire format enough to crash
/// a whole list over one unexpected type.
String asId(Object? value) => value?.toString() ?? '';

/// Counts arrive as JSON numbers, but a missing or unexpected type must be
/// distinguishable from a real zero -- the caller supplies the fallback.
int? asInt(Object? value) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  if (value is String) return int.tryParse(value);
  return null;
}

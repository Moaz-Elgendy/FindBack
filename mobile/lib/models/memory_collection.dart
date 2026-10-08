import 'dart:math';

class MemoryCollection {
  const MemoryCollection({required this.id, required this.name, required this.urls});
  final String id, name;
  final List<String> urls;
  factory MemoryCollection.fromJson(Map<String, dynamic> json) => MemoryCollection(
    id: json['id'] as String, name: json['name'] as String,
    urls: (json['urls'] as List).cast<String>(),
  );
  Map<String, Object?> toJson() => {'name': name, 'urls': urls};
}

String newCollectionId() {
  final random = Random.secure();
  final bytes = List.generate(16, (_) => random.nextInt(256));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
}

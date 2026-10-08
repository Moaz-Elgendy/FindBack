class MemoryReminder {
  const MemoryReminder({required this.itemId, required this.scheduledAt,
    required this.timeZone, this.notificationId = 0, this.delivered = false,
    this.dirty = '', this.suspended = false, this.scheduled = false});
  final String itemId, timeZone, dirty;
  final DateTime scheduledAt;
  final int notificationId;
  final bool delivered, suspended, scheduled;

  factory MemoryReminder.fromJson(Map<String, dynamic> json) => MemoryReminder(
    itemId: json['item_id'] as String,
    scheduledAt: DateTime.parse(json['scheduled_at'] as String).toUtc(),
    timeZone: json['time_zone'] as String,
    notificationId: json['notification_id'] as int? ?? 0,
    delivered: json['delivered_at'] != null || json['delivered'] == 1,
    dirty: json['dirty'] as String? ?? '', suspended: json['suspended'] == 1, scheduled: json['scheduled'] == 1);
  Map<String, dynamic> toJson() => {'scheduled_at': scheduledAt.toUtc().toIso8601String(), 'time_zone': timeZone};
}

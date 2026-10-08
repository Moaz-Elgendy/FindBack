class WeeklyNoteSettings {
  const WeeklyNoteSettings({this.enabled = false, this.weekday = 6,
    this.hour = 18, this.minute = 0, this.timeZone = 'UTC'});
  final bool enabled;
  final int weekday, hour, minute;
  final String timeZone;

  factory WeeklyNoteSettings.fromJson(Map<String, dynamic> json) => WeeklyNoteSettings(
    enabled: json['enabled'] as bool, weekday: json['weekday'] as int,
    hour: json['hour'] as int, minute: json['minute'] as int,
    timeZone: json['time_zone'] as String);

  Map<String, Object?> toJson() => {'enabled': enabled, 'weekday': weekday,
    'hour': hour, 'minute': minute, 'time_zone': timeZone};

  WeeklyNoteSettings withSchedule({bool? enabled, int? weekday, int? hour, int? minute, String? timeZone}) =>
    WeeklyNoteSettings(enabled: enabled ?? this.enabled, weekday: weekday ?? this.weekday,
      hour: hour ?? this.hour, minute: minute ?? this.minute, timeZone: timeZone ?? this.timeZone);
}

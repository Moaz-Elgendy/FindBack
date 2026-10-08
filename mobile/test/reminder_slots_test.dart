import 'package:flutter_test/flutter_test.dart';
import 'package:timezone/data/latest.dart' as data;
import 'package:timezone/timezone.dart' as tz;
import 'package:findback/services/reminder_slots.dart';

void main() {
  data.initializeTimeZones();
  final zone = tz.getLocation('America/New_York');
  List<ReminderSlot> slots(int day, int hour, int minute) => reminderSlots(tz.TZDateTime(zone, 2026, 10, day, hour, minute), zone);
  test('late-night, evening cutoff, Saturday and Sunday', () {
    expect(slots(8, 0, 30)[1].label, 'Today');
    expect(slots(8, 0, 30)[1].time.day, 8);
    expect(slots(8, 17, 30).first.label, 'This evening');
    expect(slots(8, 18, 0).first.label, 'Tomorrow');
    expect(slots(8, 18, 30).first.label, 'Tomorrow');
    expect(slots(10, 12, 0).last.label, 'Next Saturday');
    expect(slots(10, 12, 0).last.time.day, 17);
    expect(slots(11, 12, 0).last.time.day, 17);
  });
  test('calendar slots honor DST and current device zone', () {
    final before = tz.TZDateTime(zone, 2026, 3, 7, 12);
    final tomorrow = reminderSlots(before, zone)[1].time;
    expect(tomorrow.hour, 9);
    expect(tomorrow.timeZoneOffset, const Duration(hours: -4));
    expect(tomorrow.toUtc(), DateTime.utc(2026, 3, 8, 13));
    final current = tz.getLocation('Asia/Tokyo');
    expect(reminderSlots(before, current).first.time.location.name, 'Asia/Tokyo');
  });
}

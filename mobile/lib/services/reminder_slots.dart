import 'package:timezone/timezone.dart' as tz;
import 'package:timezone/data/latest.dart' as data;

tz.Location reminderZone(String name) {
  if (tz.timeZoneDatabase.locations.isEmpty) data.initializeTimeZones();
  return name == 'UTC' ? tz.UTC : tz.getLocation(name);
}

class ReminderSlot {
  const ReminderSlot(this.label, this.time);
  final String label;
  final tz.TZDateTime time;
}

List<ReminderSlot> reminderSlots(DateTime instant, tz.Location zone) {
  final now = tz.TZDateTime.from(instant, zone);
  tz.TZDateTime at(int dayOffset, int hour) => tz.TZDateTime(zone, now.year, now.month, now.day + dayOffset, hour);
  final evening = at(0, 19);
  // 00:00–04:59 still belongs to "tonight": offer today's 09:00.
  final tonight = now.hour < 5;
  final saturday = (DateTime.saturday - now.weekday + 7) % 7;
  return [
    if (evening.difference(now) > const Duration(hours: 1)) ReminderSlot('This evening', evening),
    ReminderSlot(tonight ? 'Today' : 'Tomorrow', at(tonight ? 0 : 1, 9)),
    ReminderSlot(saturday == 0 ? 'Next Saturday' : 'Saturday', at(saturday == 0 ? 7 : saturday, 10)),
  ];
}

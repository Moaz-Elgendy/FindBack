import 'package:flutter/material.dart';
import '../../widgets/feedback.dart';
import 'package:timezone/timezone.dart' as tz;
import '../../models/item.dart';
import '../../models/reminder.dart';
import '../../services/reminder_service.dart';
import '../../services/reminder_slots.dart';
import '../../theme.dart';

class ReminderButton extends StatefulWidget {
  const ReminderButton({super.key, required this.item, required this.service});
  final ItemDetail item;
  final ReminderService service;
  @override State<ReminderButton> createState() => _ReminderButtonState();
}

class _ReminderButtonState extends State<ReminderButton> {
  MemoryReminder? _reminder;
  tz.Location? _deviceZone;
  bool _busy = false;
  @override void initState() { super.initState(); widget.service.addListener(_load); _load(); }
  @override void dispose() { widget.service.removeListener(_load); super.dispose(); }
  Future<void> _load() async {
    try {
      final value = await widget.service.current(widget.item.id);
      final zone = reminderZone(await widget.service.notifications.deviceZone());
      if (mounted) setState(() { _reminder = value; _deviceZone = zone; });
    } catch (_) { /* A closed account cache must not update the previous route. */ }
  }
  String _time(DateTime at) => MaterialLocalizations.of(context).formatTimeOfDay(TimeOfDay.fromDateTime(at));
  String _label(MemoryReminder value) {
    final at = tz.TZDateTime.from(value.scheduledAt, _deviceZone ?? reminderZone(value.timeZone));
    final now = tz.TZDateTime.from(widget.service.clock(), at.location);
    final tomorrow = tz.TZDateTime(at.location, now.year, now.month, now.day + 1);
    final day = at.year == now.year && at.month == now.month && at.day == now.day ? 'Today' :
      at.year == tomorrow.year && at.month == tomorrow.month && at.day == tomorrow.day ? 'Tomorrow' :
      MaterialLocalizations.of(context).formatShortDate(at);
    return '$day, ${_time(at)}';
  }
  Future<bool> _explain() async => await showDialog<bool>(context: context, builder: (context) => AlertDialog(
    title: const Text('Get your reminder'),
    content: const Text("We'll send one notification at the time you choose."),
    actions: [TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Not now')),
      FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Continue'))])) ?? false;

  Future<void> _open() async {
    setState(() => _busy = true);
    try {
      final zone = reminderZone(await widget.service.notifications.deviceZone());
      if (!mounted) return;
      final slots = reminderSlots(widget.service.clock(), zone);
      final choice = await showModalBottomSheet<int>(context: context, useSafeArea: true, showDragHandle: true,
        isScrollControlled: true, builder: (sheet) => SingleChildScrollView(child: Padding(
          padding: const EdgeInsetsDirectional.fromSTEB(20, 0, 20, 24), child: Column(mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('Remind me', style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: 12),
            for (var i = 0; i < slots.length; i++) ListTile(contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.schedule), title: Text('${slots[i].label}, ${_time(slots[i].time)}'),
              onTap: () => Navigator.pop(sheet, i)),
            ListTile(contentPadding: EdgeInsets.zero, leading: const Icon(Icons.calendar_today_outlined),
              title: const Text('Pick date and time'), onTap: () => Navigator.pop(sheet, -1)),
            if (_reminder != null) ListTile(contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.notifications_off_outlined), title: const Text('Remove reminder'),
              onTap: () => Navigator.pop(sheet, -2)),
            Align(alignment: AlignmentDirectional.centerEnd, child: TextButton(
              onPressed: () => Navigator.pop(sheet), child: const Text('Cancel'))),
          ]))));
      if (!mounted || choice == null) return;
      if (choice == -2) {
        await widget.service.remove(widget.item.id);
        if (mounted) _toast('Reminder removed');
        return;
      }
      DateTime? at;
      if (choice >= 0) { at = slots[choice].time; }
      else {
        final now = tz.TZDateTime.from(widget.service.clock(), zone);
        final date = await showDatePicker(context: context, initialDate: now,
          firstDate: DateTime(now.year, now.month, now.day), lastDate: DateTime(now.year + 10));
        if (!mounted || date == null) return;
        final time = await showTimePicker(context: context, initialTime: TimeOfDay.fromDateTime(now));
        if (!mounted || time == null) return;
        final chosen = tz.TZDateTime(zone, date.year, date.month, date.day, time.hour, time.minute);
        if (chosen.hour != time.hour || chosen.minute != time.minute) {
          _toast('That time does not exist when the clocks change. Choose another time.');
          return;
        }
        at = chosen;
      }
      if (!at.isAfter(widget.service.clock())) {
        _toast('Choose a future time'); return;
      }
      final allowed = await widget.service.set(widget.item, at, zone.name, explainPermission: _explain);
      if (!mounted) return;
      if (allowed) { _toast('Reminder set'); }
      else { showFindBackToast(context,
        'Notifications are off. Turn them on in Settings to get this reminder',
        action: SnackBarAction(label: 'Open settings', onPressed: () async {
          try { await widget.service.notifications.openSettings(); }
          catch (_) { if (mounted) _toast('Could not open Settings. Open your phone settings to allow notifications.'); }
        })); }
    } catch (_) {
      if (mounted) _toast('Could not schedule this reminder. Your saved choice is kept; try again.');
    } finally { if (mounted) setState(() => _busy = false); }
  }
  void _toast(String text) => showFindBackToast(context, text);
  @override Widget build(BuildContext context) {
    final colors = Theme.of(context).brightness == Brightness.dark ? FindBackTheme.dark : FindBackTheme.light;
    return OutlinedButton.icon(onPressed: _busy ? null : _open,
      style: OutlinedButton.styleFrom(alignment: AlignmentDirectional.centerStart,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 15),
        textStyle: Theme.of(context).textTheme.labelLarge?.copyWith(fontSize: 15.5, fontWeight: FontWeight.w600),
        side: BorderSide(color: colors[_reminder == null ? FindBackColor.line : FindBackColor.accent]!),
        backgroundColor: colors[_reminder == null ? FindBackColor.card : FindBackColor.accentSoft],
        foregroundColor: colors[_reminder == null ? FindBackColor.ink : FindBackColor.accentInk]),
      icon: Icon(_reminder == null ? Icons.notifications_none : Icons.notifications_active_outlined),
      label: Text(_busy ? 'Setting reminder…' : _reminder == null ? 'Remind me' : 'Reminder · ${_label(_reminder!)}'));
  }
}

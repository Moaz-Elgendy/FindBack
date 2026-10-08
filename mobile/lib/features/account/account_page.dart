import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:share_plus/share_plus.dart';
import '../../app_services.dart';
import '../../data/api_client.dart';
import '../../models/item.dart';
import '../../models/weekly_note_settings.dart';
import '../../services/account_coordinator.dart';
import '../../services/auth_service.dart';
import '../../services/appearance.dart';
import '../../services/reminder_notifications.dart';

class AccountPage extends StatefulWidget {
  const AccountPage({super.key, required this.auth, this.recovery = false,
    this.api, this.notifications, this.onDelete, this.onExport});
  final AuthService auth;
  final bool recovery;
  final ApiClient? api;
  final ReminderNotifications? notifications;
  final Future<void> Function()? onDelete;
  final Future<void> Function(Map<String, dynamic>)? onExport;
  @override
  State<AccountPage> createState() => _AccountPageState();
}

class _AccountPageState extends State<AccountPage> {
  final _form = GlobalKey<FormState>();
  final _email = TextEditingController();
  final _password = TextEditingController();
  bool _signup = false, _busy = false, _recovering = false;
  String? _message;
  ApiClient? _api;
  ReminderNotifications? _notifications;
  AppServices? _services;
  WeeklyNoteSettings? _weekly;
  String? _loadedFor;
  bool _loading = false, _loadFailed = false;
  int _loadGeneration = 0;
  static const _days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  @override
  void initState() {
    super.initState();
    _recovering = widget.recovery;
    widget.auth.session.addListener(_sessionChanged);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _bindServices();
  }

  @override
  void didUpdateWidget(AccountPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.api != oldWidget.api || widget.notifications != oldWidget.notifications) _bindServices();
  }

  void _bindServices() {
    _services = AppServicesScope.maybeOf(context);
    final nextApi = widget.api ?? _services?.api;
    if (nextApi != _api) {
      _loadedFor = null;
      _weekly = null;
      _loadGeneration++;
    }
    _api = nextApi;
    _notifications = widget.notifications ?? _services?.reminders?.notifications;
    _loadAccount();
  }

  void _sessionChanged() {
    _loadGeneration++;
    _loadedFor = null;
    _weekly = null;
    _loading = false;
    _loadFailed = false;
    _loadAccount();
  }

  Future<void> _loadAccount() async {
    final id = widget.auth.currentSession?.id;
    if (_recovering || id == null || _api == null || _loadedFor == id) return;
    _loadedFor = id;
    _loading = true;
    _loadFailed = false;
    final generation = ++_loadGeneration;
    try {
      final value = await _api!.weeklyNoteSettings();
      if (mounted && generation == _loadGeneration && widget.auth.currentSession?.id == id) setState(() => _weekly = value);
    } catch (_) {
      if (mounted && generation == _loadGeneration && widget.auth.currentSession?.id == id) setState(() => _loadFailed = true);
    } finally {
      if (mounted && generation == _loadGeneration && widget.auth.currentSession?.id == id) setState(() => _loading = false);
    }
  }

  @override
  void dispose() {
    widget.auth.session.removeListener(_sessionChanged);
    _email.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() action,
      {String failureMessage = 'Could not update your account. Please try again.'}) async {
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      await action();
    } on AuthException catch (error) {
      if (mounted) setState(() => _message = error.message);
    } on ApiException catch (error) {
      if (mounted) setState(() => _message = error.message);
    } catch (_) {
      if (mounted) {
        setState(() =>
            _message = failureMessage);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _toast(String message) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));

  void _checkAccount(int generation, String? id) {
    if (!mounted || generation != _loadGeneration || widget.auth.currentSession?.id != id) {
      throw const AuthException('Your account changed. Please try again.');
    }
  }

  Future<void> _setWeekly(bool enabled) async {
    final notifications = _notifications;
    if (notifications == null || _weekly == null) return;
    final generation = _loadGeneration, id = widget.auth.currentSession?.id;
    final api = _api!, choice = _weekly!;
    await _run(() async {
      final allowed = !enabled || await notifications.enabled();
      _checkAccount(generation, id);
      if (!allowed) {
        if (!mounted) return;
        final explain = await showDialog<bool>(context: context, builder: (context) => AlertDialog(
          scrollable: true,
          title: const Text('A weekly note'),
          content: const Text("We'll send one notification a week to help you revisit forgotten saves."),
          actions: [TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Not now')),
            FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Continue'))]));
        if (explain != true) return;
      }
      final zone = enabled ? await notifications.deviceZone() : choice.timeZone;
      _checkAccount(generation, id);
      final value = await api.saveWeeklyNoteSettings(choice.withSchedule(enabled: enabled, timeZone: zone));
      _checkAccount(generation, id);
      setState(() => _weekly = value);
      if (!allowed && !await notifications.requestPermission() && mounted && generation == _loadGeneration) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: const Text('Notifications are off. Turn them on in Settings to get your weekly note'),
          action: SnackBarAction(label: 'Open settings', onPressed: () async {
            try { await notifications.openSettings(); }
            catch (_) { if (mounted) _toast('Could not open Settings. Open your phone settings to allow notifications.'); }
          })));
      }
    });
  }

  Future<void> _pickSchedule() async {
    final value = _weekly;
    if (value == null) return;
    final generation = _loadGeneration, id = widget.auth.currentSession?.id;
    final api = _api!, notifications = _notifications!;
    final day = await showModalBottomSheet<int>(context: context, useSafeArea: true,
      showDragHandle: true, isScrollControlled: true, builder: (context) => SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Padding(padding: const EdgeInsets.all(16), child: Text('Weekly note day', style: Theme.of(context).textTheme.titleLarge)),
          for (var i = 0; i < _days.length; i++) ListTile(title: Text(_days[i]),
            trailing: i == value.weekday ? const Icon(Icons.check) : null,
            onTap: () => Navigator.pop(context, i))])));
    if (day == null || !mounted || generation != _loadGeneration) return;
    final time = await showTimePicker(context: context, initialTime: TimeOfDay(hour: value.hour, minute: value.minute));
    if (time == null || !mounted) return;
    await _run(() async {
      _checkAccount(generation, id);
      final zone = await notifications.deviceZone();
      _checkAccount(generation, id);
      final saved = await api.saveWeeklyNoteSettings(value.withSchedule(
        weekday: day, hour: time.hour, minute: time.minute, timeZone: zone));
      _checkAccount(generation, id);
      setState(() => _weekly = saved);
    });
  }

  Future<void> _export() => _run(() async {
    final generation = _loadGeneration, id = widget.auth.currentSession?.id;
    final api = _api, services = _services;
    final signedIn = id != null;
    final data = signedIn ? await api!.exportSaves() : <String, dynamic>{
      'version': 1, 'exported_at': DateTime.now().toUtc().toIso8601String(), 'saves': <dynamic>[]};
    _checkAccount(generation, id);
    if (services != null) {
      final local = await services.db.db.query('items');
      _checkAccount(generation, id);
      final saves = data['saves'] as List;
      for (final row in local) {
        final item = ItemDetail.fromLocalRow(row);
        if (signedIn && !item.id.startsWith('local-')) continue;
        if (saves.any((save) => save['url'] == item.url)) continue;
        saves.add({'id': item.id, 'url': item.url, 'title': item.title,
          'summary': item.summary, 'saved_at': item.createdAt?.toUtc().toIso8601String(),
          'type': item.contentType, 'tags': item.tags, 'brief': {
            'instant_brief': item.instantBrief, 'best_takeaway': item.bestTakeaway,
            'key_points_with_refs': item.pointsWithRefs.map((point) => point.toJson()).toList()}});
      }
    }
    _checkAccount(generation, id);
    if (widget.onExport != null) { await widget.onExport!(data); return; }
    if (!mounted) return;
    final box = context.findRenderObject() as RenderBox?;
    await SharePlus.instance.share(ShareParams(
      files: [XFile.fromData(Uint8List.fromList(utf8.encode(const JsonEncoder.withIndent('  ').convert(data))), mimeType: 'application/json')],
      fileNameOverrides: ['findback-saves.json'], subject: 'My FindBack saves',
      sharePositionOrigin: box == null ? null : box.localToGlobal(Offset.zero) & box.size));
  }, failureMessage: 'Could not export your saves. Please try again.');

  Future<void> _deleteAccount() async {
    final action = widget.onDelete ?? AccountCoordinatorScope.maybeOf(context)?.deleteAccount;
    if (action == null) return;
    final generation = _loadGeneration, id = widget.auth.currentSession?.id;
    final confirmed = await showDialog<bool>(context: context, builder: (context) => AlertDialog(
      scrollable: true,
      title: const Text('Delete your account?'),
      content: const Text('Your account, saves and summaries will be permanently removed. This cannot be undone.'),
      actions: [TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
        FilledButton(style: FilledButton.styleFrom(backgroundColor: Theme.of(context).colorScheme.error),
          onPressed: () => Navigator.pop(context, true), child: const Text('Delete account'))]));
    if (confirmed == true && mounted) {
      await _run(() async {
        _checkAccount(generation, id);
        await action();
      }, failureMessage: 'Could not delete your account. Please try again.');
    }
  }

  Widget _heading(String label) => Padding(padding: const EdgeInsetsDirectional.fromSTEB(0, 24, 0, 8),
    child: Text(label, style: Theme.of(context).textTheme.titleSmall));

  Widget _group(List<Widget> children) => Card(margin: EdgeInsets.zero, elevation: 0, child: Padding(
    padding: const EdgeInsets.all(16), child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: children)));

  Future<void> _submit() async {
    if (!_form.currentState!.validate()) return;
    await _run(() async {
      if (_recovering) {
        await widget.auth.updatePassword(_password.text);
        if (mounted) {
          setState(() {
            _recovering = false;
            _message = 'Password updated.';
          });
        }
      } else if (_signup) {
        final message = await widget.auth.signUp(_email.text, _password.text);
        if (mounted) setState(() => _message = message);
      } else {
        await widget.auth.signIn(_email.text, _password.text);
      }
      _password.clear();
    });
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Account')),
        body: SafeArea(
            child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ValueListenableBuilder<AuthSession?>(
            valueListenable: widget.auth.session,
            builder: (context, session, _) => Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (session != null && !_recovering) ...[
                  _group([
                    Row(children: [
                      const CircleAvatar(radius: 24, child: Icon(Icons.person_outline)),
                      const SizedBox(width: 12),
                      Expanded(child: Text(session.email, style: Theme.of(context).textTheme.titleMedium)),
                    ]),
                    const SizedBox(height: 8),
                    Align(alignment: AlignmentDirectional.centerStart, child: TextButton(
                      onPressed: _busy ? null : () => _run(widget.auth.signOut), child: const Text('Sign out'))),
                  ]),
                ] else ...[
                  Text(_recovering
                      ? 'Set a new password'
                      : 'Sign in or create an account. You can keep using FindBack as a guest.'),
                  const SizedBox(height: 24),
                  Form(
                      key: _form,
                      child: Column(children: [
                        if (!_recovering)
                          TextFormField(
                              controller: _email,
                              enabled: !_busy,
                              decoration:
                                  const InputDecoration(labelText: 'Email', errorMaxLines: 3),
                              keyboardType: TextInputType.emailAddress,
                              autofillHints: const [AutofillHints.email],
                              autocorrect: false,
                              validator: (value) {
                                try {
                                  AuthService.validateEmail(value ?? '');
                                  return null;
                                } on AuthException catch (error) {
                                  return error.message;
                                }
                              }),
                        const SizedBox(height: 16),
                        TextFormField(
                            controller: _password,
                            enabled: !_busy,
                            obscureText: true,
                            decoration: InputDecoration(
                                errorMaxLines: 3,
                                labelText:
                                    _recovering ? 'New password' : 'Password'),
                            autofillHints: [
                              _signup || _recovering
                                  ? AutofillHints.newPassword
                                  : AutofillHints.password
                            ],
                            validator: (value) {
                              try {
                                AuthService.validatePassword(value ?? '');
                                return null;
                              } on AuthException catch (error) {
                                return error.message;
                              }
                            }),
                      ])),
                  const SizedBox(height: 24),
                  FilledButton(
                      onPressed: _busy ? null : _submit,
                      child: Text(_recovering
                          ? 'Update password'
                          : _signup
                              ? 'Create account'
                              : 'Sign in')),
                  if (!_recovering) ...[
                    TextButton(
                        onPressed: _busy
                            ? null
                            : () => setState(() {
                                  _signup = !_signup;
                                  _message = null;
                                }),
                        child: Text(_signup
                            ? 'Already have an account? Sign in'
                            : 'Create an account')),
                    TextButton(
                        onPressed: _busy
                            ? null
                            : () => _run(() async {
                                  await widget.auth
                                      .sendPasswordReset(_email.text);
                                  if (mounted) {
                                    setState(() => _message =
                                        'If an account exists, check your email for a reset link.');
                                  }
                                }),
                        child: const Text('Forgot password?')),
                  ],
                ],
                if (!_recovering) ...[
                  _heading('Weekly note'),
                  _group([
                    SwitchListTile(contentPadding: EdgeInsets.zero,
                      title: const Text('Remind me of forgotten saves'),
                      subtitle: Text(session == null ? 'Sign in to receive a weekly note.' : 'One notification a week. Never daily.'),
                      value: _weekly?.enabled ?? false,
                      onChanged: _busy || _loading || _weekly == null || _notifications == null ? null : _setWeekly),
                    if (_loading) const LinearProgressIndicator(),
                    if (_loadFailed) ...[
                      const Text('Could not load your weekly note settings.'),
                      Align(alignment: AlignmentDirectional.centerStart, child: TextButton(
                        onPressed: _busy ? null : () { setState(() => _loadedFor = null); _loadAccount(); },
                        child: const Text('Try again'))),
                    ],
                    if (_weekly?.enabled == true) ListTile(contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.schedule),
                      title: Text('${_days[_weekly!.weekday]} at ${MaterialLocalizations.of(context).formatTimeOfDay(TimeOfDay(hour: _weekly!.hour, minute: _weekly!.minute))}'),
                      subtitle: Text(_weekly!.timeZone),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: _busy || _notifications == null ? null : _pickSchedule),
                  ]),
                  if (AppearanceScope.maybeOf(context) case final appearance?) ...[
                    _heading('Appearance'),
                    _group([
                      Text('Theme', style: Theme.of(context).textTheme.titleMedium),
                      const SizedBox(height: 8),
                      Wrap(spacing: 8, runSpacing: 8, children: [
                        for (final option in const {ThemeMode.light: 'Light', ThemeMode.dark: 'Dark', ThemeMode.system: 'Auto'}.entries)
                          ChoiceChip(label: Text(option.value), selected: appearance.mode == option.key,
                            onSelected: _busy ? null : (_) => _run(() => appearance.select(option.key),
                              failureMessage: 'Could not save appearance. Try again.')),
                      ]),
                    ]),
                  ],
                  _heading('Your data'),
                  _group([
                    ListTile(contentPadding: EdgeInsets.zero, leading: const Icon(Icons.download_outlined),
                      title: const Text('Export my saves'), trailing: const Icon(Icons.chevron_right),
                      onTap: _busy || (session == null ? _services == null : _api == null) ? null : _export),
                    if (session != null) ...[
                      const Divider(),
                      ListTile(contentPadding: EdgeInsets.zero,
                        leading: Icon(Icons.delete_outline, color: Theme.of(context).colorScheme.error),
                        title: Text('Delete account', style: TextStyle(color: Theme.of(context).colorScheme.error)),
                        subtitle: const Text('Removes your saves and summaries.'),
                        onTap: _busy || (widget.onDelete == null && AccountCoordinatorScope.maybeOf(context) == null) ? null : _deleteAccount),
                    ],
                  ]),
                  Padding(padding: const EdgeInsetsDirectional.only(top: 20), child: Text(
                    'Links you save are read and summarized by an AI service. Nothing is shared with other people.',
                    style: Theme.of(context).textTheme.bodySmall)),
                ],
                if (_busy) Padding(padding: const EdgeInsets.only(top: 16),
                  child: Semantics(liveRegion: true, child: const Text('Updating your account…'))),
                if (_message != null)
                  Padding(
                      padding: const EdgeInsets.only(top: 16),
                      child:
                          Semantics(liveRegion: true, child: Text(_message!))),
              ],
            ),
          ),
        )),
      );
}

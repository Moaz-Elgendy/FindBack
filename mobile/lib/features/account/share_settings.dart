import 'package:flutter/material.dart';
import '../../data/api_client.dart';
import '../../widgets/feedback.dart';

class ShareSettings extends StatefulWidget {
  const ShareSettings({super.key, required this.api});
  final ApiClient api;
  @override
  State<ShareSettings> createState() => _ShareSettingsState();
}

class _ShareSettingsState extends State<ShareSettings> {
  final _name = TextEditingController();
  List<Map<String, dynamic>> _shares = [];
  bool _loading = true, _busy = false, _loaded = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final profile = await widget.api.shareProfile();
      final shares = await widget.api.activeShares();
      if (!mounted) return;
      setState(() {
        _name.text = profile['display_name'] as String? ?? '';
        _shares = shares;
        _loaded = true;
        _error = null;
      });
    } catch (_) {
      if (mounted) setState(() => _error = 'Could not load sharing settings.');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
      if (mounted) setState(() => _error = null);
    } catch (_) {
      if (mounted) setState(() => _error = 'Use a valid name and try again, or check your connection.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      Text('Sharing', style: Theme.of(context).textTheme.titleMedium),
      const SizedBox(height: 12),
      if (_loading) const LinearProgressIndicator()
      else if (_error != null) ...[
        Text(_error!),
        TextButton(onPressed: _busy ? null : _load, child: const Text('Try again')),
      ],
      if (!_loading && _loaded) ...[
        TextField(controller: _name, maxLength: 80, enabled: !_busy,
          decoration: const InputDecoration(labelText: 'Display name (optional)',
            helperText: 'Shown on memories you share. Never your email.')),
        Align(alignment: AlignmentDirectional.centerEnd, child: TextButton(
          onPressed: _busy ? null : () => _run(() async {
            final name = _name.text.trim();
            if (name.contains('@')) {
              throw const FormatException('Use a name, not an email address.');
            }
            await widget.api.saveDisplayName(name.isEmpty ? null : name);
            if (!context.mounted) return;
            showFindBackToast(context, 'Display name saved');
          }), child: const Text('Save name'))),
        const Text('Active share links'),
        if (_shares.isEmpty) const Padding(padding: EdgeInsets.symmetric(vertical: 8),
          child: Text('No active share links.')),
        for (final share in _shares) ListTile(
          contentPadding: EdgeInsets.zero,
          title: Text(share['title'] as String? ?? 'Shared memory', maxLines: 2, overflow: TextOverflow.ellipsis),
          subtitle: Text('Expires ${MaterialLocalizations.of(context).formatCompactDate(DateTime.parse(share['expires_at'] as String).toLocal())}'),
          trailing: TextButton(onPressed: _busy ? null : () => _run(() async {
            await widget.api.revokeShare(share['id'] as String);
            if (mounted) setState(() => _shares.remove(share));
          }), child: const Text('Revoke')),
        ),
        const Text('Expiry and revocation never affect copies someone has already saved.'),
      ],
    ],
  );
}

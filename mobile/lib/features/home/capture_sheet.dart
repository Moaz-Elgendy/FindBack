import 'package:flutter/material.dart';

import '../../data/api_client.dart';
import '../../services/capture_service.dart';
import '../../utils/share_text.dart';

/// "Save a link" sheet: paste anything a share sheet or browser gives you and
/// we pull the URL out of it. Works with no network — the capture is queued.
class CaptureSheet extends StatefulWidget {
  const CaptureSheet({super.key, required this.capture, this.onSaved});

  final CaptureService capture;
  final void Function(CaptureBatch outcome)? onSaved;

  static Future<void> show(
    BuildContext context, {
    required CaptureService capture,
    void Function(CaptureBatch outcome)? onSaved,
  }) =>
      showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        showDragHandle: true,
        builder: (BuildContext context) => Padding(
          padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
          child: SingleChildScrollView(
            child: CaptureSheet(capture: capture, onSaved: onSaved),
          ),
        ),
      );

  @override
  State<CaptureSheet> createState() => _CaptureSheetState();
}

class _CaptureSheetState extends State<CaptureSheet> {
  final TextEditingController _text = TextEditingController();
  final TextEditingController _hint = TextEditingController();
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _text.dispose();
    _hint.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final String raw = _text.text.trim();
    final urls = extractUrlsFromShareText(raw);
    if (urls.isEmpty) {
      setState(() => _error = 'No link found in that text. Paste a URL and try again.');
      return;
    }
    final String? hint = _hint.text.trim().isEmpty ? null : _hint.text.trim();

    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final CaptureBatch outcome = await widget.capture.captureText(raw, titleHint: hint);
      widget.onSaved?.call(outcome);
      if (!mounted) return;
      if (outcome.failedUrls.isEmpty) {
        Navigator.pop(context);
      } else {
        _text.text = outcome.failedUrls.join('\n');
        setState(() {
          _saving = false;
          _error = '${outcome.outcomes.length} saved. ${outcome.failedUrls.length} could not be saved; retry the remaining links.';
        });
      }
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _saving = false;
        _error = error.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Text('Save a link', style: theme.textTheme.titleLarge),
          const SizedBox(height: 4),
          Text(
            'Paste one or more links, or a whole share message.',
            style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _text,
            autofocus: true,
            minLines: 2,
            maxLines: 4,
            keyboardType: TextInputType.url,
            decoration: const InputDecoration(
              labelText: 'Link or shared text',
              hintText: 'https://example.com/…',
            ),
          ),
          const SizedBox(height: 12),
          Text('What you remember about it (optional)', style: theme.textTheme.bodyMedium),
          const SizedBox(height: 8),
          Semantics(label: 'What you remember about it (optional)', child: TextField(
            controller: _hint,
            textCapitalization: TextCapitalization.sentences,
            decoration: const InputDecoration(
              helperText: 'Becomes the title until the server reads the page',
              helperMaxLines: 3,
            ),
            )),
          if (_error != null) ...<Widget>[
            const SizedBox(height: 12),
            Text(
              _error!,
              style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
            ),
          ],
          const SizedBox(height: 20),
          Row(children: [
            Expanded(child: TextButton(onPressed: _saving ? null : () => Navigator.pop(context), child: const Text('Cancel'))),
            const SizedBox(width: 12),
            Expanded(child: FilledButton(
            onPressed: _saving ? null : _save,
            child: _saving
                ? const SizedBox(
                    height: 18,
                    width: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('Save'),
          )),
          ]),
        ],
      ),
    );
  }
}

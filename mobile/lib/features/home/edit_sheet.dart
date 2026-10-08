import 'package:flutter/material.dart';

import '../../data/api_client.dart';

class EditSheet extends StatefulWidget {
  const EditSheet(
      {super.key,
      required this.title,
      required this.brief,
      required this.onSave});

  final String title, brief;
  final Future<void> Function(String title, String brief) onSave;

  static Future<void> show(BuildContext context,
          {required String title,
          required String brief,
          required Future<void> Function(String title, String brief) onSave}) =>
      showModalBottomSheet<void>(
          context: context,
          isScrollControlled: true,
          useSafeArea: true,
          showDragHandle: true,
          builder: (context) => Padding(
              padding: EdgeInsets.only(
                  bottom: MediaQuery.viewInsetsOf(context).bottom),
              child: SingleChildScrollView(
                  child:
                      EditSheet(title: title, brief: brief, onSave: onSave))));

  @override
  State<EditSheet> createState() => _EditSheetState();
}

class _EditSheetState extends State<EditSheet> {
  final _form = GlobalKey<FormState>();
  late final _title = TextEditingController(text: widget.title);
  late final _brief = TextEditingController(text: widget.brief);
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _title.dispose();
    _brief.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_form.currentState!.validate()) return;
    FocusScope.of(context).unfocus();
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await widget.onSave(_title.text.trim(), _brief.text);
      if (mounted) {
        setState(() => _saving = false);
        Navigator.pop(context);
      }
    } catch (error) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error = error is ApiException
              ? error.message
              : 'Could not save changes. Try again.';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
      canPop: !_saving,
      child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
          child: Form(
              key: _form,
              child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text('Edit memory',
                        style: Theme.of(context).textTheme.titleLarge),
                    const SizedBox(height: 20),
                    TextFormField(
                        controller: _title,
                        enabled: !_saving,
                        autofocus: true,
                        maxLength: 500,
                        textCapitalization: TextCapitalization.sentences,
                        decoration: const InputDecoration(
                            labelText: 'Title', counterText: ''),
                        validator: (value) =>
                            value!.trim().isEmpty ? 'Enter a title.' : null),
                    const SizedBox(height: 16),
                    TextFormField(
                        controller: _brief,
                        enabled: !_saving,
                        minLines: 3,
                        maxLines: 8,
                        maxLength: 20000,
                        textCapitalization: TextCapitalization.sentences,
                        decoration: const InputDecoration(
                            labelText: 'Brief', counterText: '')),
                    if (_error != null) ...[
                      const SizedBox(height: 12),
                      Semantics(
                          liveRegion: true,
                          child: Text(_error!,
                              style: TextStyle(
                                  color: Theme.of(context).colorScheme.error))),
                    ],
                    const SizedBox(height: 20),
                    Row(children: [
                      Expanded(
                          child: TextButton(
                              onPressed:
                                  _saving ? null : () => Navigator.pop(context),
                              child: const Text('Cancel'))),
                      const SizedBox(width: 12),
                      Expanded(
                          child: FilledButton(
                              onPressed: _saving ? null : _save,
                              child: Text(_saving ? 'Saving…' : 'Save'))),
                    ]),
                  ]))));
}

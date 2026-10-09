import 'package:flutter/material.dart';

import '../../app_services.dart';
import '../../models/item.dart';
import '../../models/search_result.dart';
import '../home/detail_page.dart';
import '../home/widgets/result_card.dart';

/// The weekly note's deep-link target (redesign addendum, section 1.3): a
/// plain list of exactly the saves that note counted, and nothing else.
///
/// The ids come from the server-side snapshot (`GET /snapshots/{id}`), so what
/// is on screen matches the count the note promised. The only chrome is the
/// AppBar's automatic back button: no filters, no refresh, no actions. Dark
/// mode comes from the app theme (the cards pick their palette from it), the
/// layout is direction-neutral, and the message body scrolls so it survives
/// large text scales.
class WorthAnotherLookPage extends StatefulWidget {
  const WorthAnotherLookPage({
    super.key,
    required this.snapshotId,
    required this.services,
  });

  final String snapshotId;
  final AppServices services;

  @override
  State<WorthAnotherLookPage> createState() => _WorthAnotherLookPageState();
}

class _WorthAnotherLookPageState extends State<WorthAnotherLookPage> {
  /// Null until the snapshot arrives; an empty list is a real answer.
  SnapshotPage? _snapshot;
  bool _unavailable = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final snapshot = await widget.services.api.snapshotItems(widget.snapshotId);
      if (!mounted) return;
      setState(() {
        _snapshot = snapshot;
      });
    } catch (_) {
      // Unknown or expired snapshot, offline, or a body we cannot read: one
      // state, because going back is the only action this screen offers.
      if (!mounted) return;
      setState(() {
        _unavailable = true;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final snapshot = _snapshot;
    final items = snapshot?.items;
    return Scaffold(
      appBar: AppBar(title: const Text('Worth another look')),
      body: _unavailable
          ? const _Notice(message: 'This list is unavailable right now.')
          : items == null
              ? const Center(child: CircularProgressIndicator())
              : items.isEmpty
                  ? const _Notice(message: 'Nothing to look back on.')
                  : ListView.builder(
                      itemCount: items.length + (snapshot!.hasUnavailable ? 1 : 0),
                      itemBuilder: (context, index) {
                        // A counted save can be deleted after the note was
                        // sent. The list would then be shorter than the number
                        // on the lock screen, which reads as a bug unless it is
                        // said out loud -- so one plain line, no control.
                        if (index == 0 && snapshot.hasUnavailable) {
                          return _ShrunkNotice(snapshot: snapshot);
                        }
                        final item = items[index - (snapshot.hasUnavailable ? 1 : 0)];
                        return ResultCard(
                          result: SearchResult.fromItem(item),
                          onTap: () => Navigator.of(context).push(
                            MaterialPageRoute<void>(
                              builder: (_) => DetailPage(
                                itemId: item.id,
                                items: widget.services.items,
                              ),
                            ),
                          ),
                        );
                      },
                    ),
    );
  }
}

/// The honest line for a snapshot whose saves are no longer all there.
///
/// It states the two numbers rather than apologising for them: the user
/// remembers the notification saying "3 things", and "Showing 2 of 3 — some
/// saves are no longer available" is the only line that reconciles the two.
class _ShrunkNotice extends StatelessWidget {
  const _ShrunkNotice({required this.snapshot});

  final SnapshotPage snapshot;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final missing = snapshot.originalCount - snapshot.availableCount;
    return Padding(
      padding: const EdgeInsetsDirectional.fromSTEB(16, 16, 16, 8),
      child: Semantics(
        // The visual line is shortened to a phrase; a screen reader is told
        // the full sentence rather than just the fragment.
        label: 'Showing ${snapshot.availableCount} of ${snapshot.originalCount} saves. '
            '$missing no longer available.',
        child: ExcludeSemantics(
          child: Text(
            'Showing ${snapshot.availableCount} of ${snapshot.originalCount} — '
            'some saves are no longer available.',
            style: theme.textTheme.bodyMedium?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
            textDirection: TextDirection.ltr,
            textAlign: TextAlign.start,
          ),
        ),
      ),
    );
  }
}

/// The message body for both "nothing here" and "nothing reachable": centred,
/// direction-neutral, and scrollable so long text at a large scale never
/// overflows the viewport.
class _Notice extends StatelessWidget {
  const _Notice({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: AlignmentDirectional.center,
      child: SingleChildScrollView(
        padding: const EdgeInsetsDirectional.all(24),
        child: Text(
          message,
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.bodyLarge,
        ),
      ),
    );
  }
}

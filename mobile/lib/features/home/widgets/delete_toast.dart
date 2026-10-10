import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';

import '../../../data/api_client.dart';
import '../../../widgets/feedback.dart';

class DeleteToast {
  static void show(BuildContext context,
      {required Future<void> Function() undo,
      Duration? remaining,
      bool localOnly = false}) {
    final lifetime =
        Duration(seconds: MediaQuery.of(context).accessibleNavigation ? 10 : 5);
    SemanticsService.sendAnnouncement(
        View.of(context),
        localOnly ? 'Deleted on this phone only. Undo' : 'Memory deleted. Undo',
        Directionality.of(context));
    final messenger = ScaffoldMessenger.of(context);
    var available = true;
    messenger.hideCurrentSnackBar();
    final toast = showFindBackToast(context,
      localOnly ? 'Deleted on this phone only' : 'Memory deleted',
      duration: lifetime,
      action: SnackBarAction(
          label: 'Undo',
          onPressed: () async {
            if (!available) return;
            available = false;
            try {
              await undo();
            } catch (error) {
              if (messenger.mounted) {
                messenger.showSnackBar(SnackBar(
                    content: Text(error is ApiException
                        ? error.message
                        : 'Could not restore this memory. Refresh your library.')));
              }
            }
          }),
    );
    // SnackBar actions normally never expire with accessible navigation enabled.
    final timer = Timer(lifetime, () {
      if (!available) return;
      available = false;
      if (messenger.mounted) messenger.removeCurrentSnackBar();
    });
    toast.closed.then((_) {
      available = false;
      timer.cancel();
    });
  }
}

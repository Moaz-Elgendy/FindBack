import 'package:flutter/material.dart';

import 'app.dart';
import 'services/account_coordinator.dart';
import 'services/appearance.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  try {
    final appearance = Appearance();
    await appearance.restore();
    final accounts = await AccountCoordinator.create();
    runApp(FindBackApp(services: accounts.services, accounts: accounts, appearance: appearance));
    await accounts.start();
  } catch (error) {
    // A broken database or keystore is unrecoverable at runtime, but the user
    // deserves to know that rather than seeing a white screen.
    debugPrint('[main] startup failed: $error');
    runApp(_FatalErrorApp(details: '$error'));
  }
}

class _FatalErrorApp extends StatelessWidget {
  const _FatalErrorApp({required this.details});

  final String details;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      home: Scaffold(
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                const Icon(Icons.error_outline, size: 40),
                const SizedBox(height: 12),
                Text(
                  'FindBack could not start.\nOn an emulator, clear the app storage and retry.',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
                const SizedBox(height: 8),
                Text(details, textAlign: TextAlign.center, style: Theme.of(context).textTheme.bodySmall),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

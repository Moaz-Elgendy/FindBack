import 'package:flutter/material.dart';

import 'app.dart';
import 'app_services.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  try {
    final AppServices services = await AppServices.create();
    runApp(FindBackApp(services: services));
    // Queue draining starts after the first frame: opening SQLite or reaching
    // the API must never delay the search box.
    await services.startSync();
    // A share that launched the app is saved here, through the same capture
    // path, so it is queued offline exactly like a typed save would be.
    await services.startShareHandling();
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

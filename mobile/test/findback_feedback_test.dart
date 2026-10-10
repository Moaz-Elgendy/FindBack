import 'package:findback/data/local_db.dart';
import 'package:findback/theme.dart';
import 'package:findback/widgets/feedback.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

Future<void> settle(WidgetTester tester) async {
  for (var i = 0; i < 8; i++) {
    await tester
        .runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
    await tester.pumpAndSettle();
  }
}

void main() {
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  testWidgets('delete opt-out applies only after confirmation and can reset',
      (tester) async {
    final db =
        (await tester.runAsync(() => LocalDb.openAt(inMemoryDatabasePath)))!;
    bool? confirmed;
    await tester.pumpWidget(MaterialApp(
        theme: FindBackTheme.build(Brightness.light),
        home: Scaffold(
            body: Builder(
                builder: (context) => FilledButton(
                    onPressed: () async =>
                        confirmed = await confirmMemoryDeletion(context, db),
                    child: const Text('Remove'))))));
    try {
      await tester.tap(find.text('Remove'));
      await settle(tester);
      expect(find.text('Delete memory?'), findsOneWidget);
      await tester.tap(find.text("Don't show this again"));
      await tester.tap(find.text('Cancel'));
      await settle(tester);
      expect(confirmed, isFalse);
      expect(await tester.runAsync(() => db.deleteConfirmationSuppressed),
          isFalse);
      await tester.tap(find.text('Remove'));
      await settle(tester);
      await tester.tap(find.text("Don't show this again"));
      await tester.tap(find.text('Delete'));
      await settle(tester);
      expect(confirmed, isTrue);
      expect(
          await tester.runAsync(() => db.deleteConfirmationSuppressed), isTrue);
      await tester.tap(find.text('Remove'));
      await settle(tester);
      expect(find.byType(AlertDialog), findsNothing);
      await tester.runAsync(() => db.setDeleteConfirmationSuppressed(false));
      await tester.tap(find.text('Remove'));
      await settle(tester);
      expect(find.text('Delete memory?'), findsOneWidget);
      await tester.tap(find.text('Cancel'));
      await settle(tester);
    } finally {
      await tester.pumpWidget(const SizedBox());
      await tester.runAsync(db.close);
    }
  });

  testWidgets('anchored menu fits narrow screens and closes on selection',
      (tester) async {
    tester.view.physicalSize = const Size(320, 640);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    var deleted = false;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: Align(
                alignment: Alignment.topRight,
                child: FindBackActionMenu(actions: [
                  FindBackAction(
                      label: 'Delete',
                      destructive: true,
                      onPressed: () => deleted = true),
                ])))));
    await tester.tap(find.byTooltip('Memory actions'));
    await tester.pumpAndSettle();
    final bounds = tester.getRect(find.text('Delete'));
    expect(bounds.left, greaterThanOrEqualTo(0));
    expect(bounds.right, lessThanOrEqualTo(320));
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();
    expect(deleted, isTrue);
    expect(find.text('Delete'), findsNothing);
  });
}

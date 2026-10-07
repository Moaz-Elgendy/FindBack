import 'package:findback/features/home/widgets/status_slots.dart';
import 'package:findback/features/home/widgets/refresh_hint.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  Widget host(Widget child, {bool reducedMotion = false}) => MaterialApp(
        home: MediaQuery(
          data: MediaQueryData(
              textScaler: TextScaler.linear(2),
              disableAnimations: reducedMotion),
          child: Scaffold(
              body: Align(alignment: Alignment.topRight, child: child)),
        ),
      );

  for (final queueFirst in [true, false]) {
    testWidgets('slots follow activation order queueFirst=$queueFirst',
        (tester) async {
      var taps = 0;
      Future<void> show(int q, int p) async {
        await tester.pumpWidget(host(
            TopBarStatus(queued: q, processing: p, onAccount: () => taps++)));
        await tester.pumpAndSettle();
      }

      await show(0, 0);
      expect(find.text('Q 0'), findsNothing);
      expect(find.text('P 0'), findsNothing);
      expect(tester.getSize(find.byType(TopBarStatus)).width, 48);
      await show(queueFirst ? 2 : 0, queueFirst ? 0 : 3);
      await show(2, 3);
      final qx = tester.getCenter(find.text('Q 2')).dx;
      final px = tester.getCenter(find.text('P 3')).dx;
      expect(queueFirst ? px < qx : qx < px, isTrue);
      expect(tester.getCenter(find.byIcon(Icons.person_outline)).dx,
          greaterThan(qx));
      expect(tester.getCenter(find.byIcon(Icons.person_outline)).dx,
          greaterThan(px));
      expect(find.byType(CircularProgressIndicator), findsNothing);
      await show(queueFirst ? 0 : 2, queueFirst ? 3 : 0);
      await show(2, 3);
      expect(
          queueFirst
              ? tester.getCenter(find.text('Q 2')).dx <
                  tester.getCenter(find.text('P 3')).dx
              : tester.getCenter(find.text('P 3')).dx <
                  tester.getCenter(find.text('Q 2')).dx,
          isTrue);
      await tester.tap(find.byTooltip('Account'));
      expect(taps, 1);
      final semantics = tester.ensureSemantics();
      expect(find.bySemanticsLabel('Queued 2'), findsOneWidget);
      expect(find.bySemanticsLabel('Processing 3'), findsOneWidget);
      semantics.dispose();
    });
  }

  testWidgets('narrow top bar keeps account at right at double text size',
      (tester) async {
    tester.view.physicalSize = const Size(320, 640);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
        host(TopBarStatus(queued: 12345, processing: 99999, onAccount: () {})));
    expect(tester.takeException(), isNull);
    expect(tester.getRect(find.byTooltip('Account')).right,
        lessThanOrEqualTo(320));
    expect(tester.getSize(find.byTooltip('Account')).width,
        greaterThanOrEqualTo(48));
    expect(find.byType(TopBarStatus), findsOneWidget);
  });

  testWidgets('hint expires and only returns after leaving top',
      (tester) async {
    var refreshes = 0;
    Future<void> show(bool atTop) => tester.pumpWidget(
        host(RefreshHint(atTop: atTop, onRefresh: () => refreshes++)));
    await show(true);
    expect(find.text('Pull down to refresh'), findsOneWidget);
    await tester.tap(find.byType(RefreshHint));
    expect(refreshes, 1);
    await tester.pump(const Duration(seconds: 3));
    expect(find.text('Pull down to refresh'), findsNothing);
    expect(tester.getSize(find.byType(RefreshHint)).height, 0);
    await show(true);
    expect(find.text('Pull down to refresh'), findsNothing);
    await show(false);
    await show(true);
    expect(find.text('Pull down to refresh'), findsOneWidget);
    await show(false);
    expect(find.text('Pull down to refresh'), findsNothing);
    await tester.pump(const Duration(seconds: 3));
    expect(tester.takeException(), isNull);
  });

  testWidgets('hint handles reduced motion, narrow layout and disposal',
      (tester) async {
    tester.view.physicalSize = const Size(320, 640);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
        host(RefreshHint(atTop: false, onRefresh: () {}), reducedMotion: true));
    expect(find.text('Pull down to refresh'), findsNothing);
    await tester.pumpWidget(
        host(RefreshHint(atTop: true, onRefresh: () {}), reducedMotion: true));
    await tester.pump();
    expect(tester.takeException(), isNull);
    final animation = tester.widget<TweenAnimationBuilder<double>>(
        find.byType(TweenAnimationBuilder<double>));
    expect(animation.duration, Duration.zero);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(seconds: 4));
    expect(tester.takeException(), isNull);
  });
}

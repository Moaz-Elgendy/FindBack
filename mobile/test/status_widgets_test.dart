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

  testWidgets(
      'one reading pill combines queue and processing and opens account',
        (tester) async {
      var taps = 0;
    await tester.pumpWidget(host(
            TopBarStatus(queued: 2, processing: 3, onAccount: () => taps++)));
    expect(find.text('Reading 5'), findsOneWidget);
      expect(find.byType(CircularProgressIndicator), findsNothing);
      await tester.tap(find.byTooltip('Account'));
      expect(taps, 1);
      final semantics = tester.ensureSemantics();
      expect(find.bySemanticsLabel('Reading 5'), findsOneWidget);
      semantics.dispose();
    await tester.pumpWidget(
        host(TopBarStatus(queued: 0, processing: 0, onAccount: () {})));
    expect(find.text('Reading 0'), findsNothing);
    expect(tester.getSize(find.byType(TopBarStatus)).width, 48);
  });

  testWidgets('RTL status fits320dp with huge count at200percent',
      (tester) async {
    tester.view.physicalSize = const Size(320, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: Directionality(
                textDirection: TextDirection.rtl,
                child: MediaQuery(
                    data:
                        const MediaQueryData(textScaler: TextScaler.linear(2)),
                    child: Row(children: [
                      const Expanded(child: Text('FindBack')),
                      TopBarStatus(
                          queued: 999999999, processing: 1, onAccount: () {})
                    ]))))));
    expect(find.text('Reading 1000000000'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

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
    await tester.pump(const Duration(seconds: 6));
    expect(tester.takeException(), isNull);
  });
}

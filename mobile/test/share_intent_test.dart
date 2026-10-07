import 'dart:async';

import 'package:findback/models/item.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/sync_service.dart' show ConnectivityProbe;
import 'package:findback/services/share_intent_service.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

/// Phase 15: Any app -> Share -> FindBack -> Saved.
///
/// The share is an entry point, not a second way of saving: whatever arrives
/// over the platform goes through the same capture path, which is what makes it
/// no more losable than a save the user typed.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const MethodChannel channel = MethodChannel(ShareIntentService.channelName);
  final List<MethodCall> log = <MethodCall>[];

  setUp(() => log.clear());
  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, null);
  });

  void mockPlatform({String? initialShare}) {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (MethodCall call) async {
      log.add(call);
      return initialShare;
    });
  }

  CaptureService captureWith({
    required Future<IngestResult> Function(String, String?, String?) ingest,
    ConnectivityProbe? isOnline,
  }) =>
      CaptureService(
        ingest: ingest,
        queue: (String url, String? preview, String? titleHint) async => 'q1',
        isOnline: isOnline ?? () async => true,
      );

  IngestResult accepted(String id) => IngestResult(
        id: id,
        canonicalUrl: 'https://example.com/x',
        status: 'pending',
      );

  test('a share that launched the app is saved', () async {
    mockPlatform(initialShare: 'Look at this https://example.com/shared');
    final ShareIntentService share = ShareIntentService(channel: channel);

    final CaptureBatch? outcome = await share.captureInitialShare(
      captureWith(ingest: (String url, _, __) async => accepted('server-1')),
    );

    expect(outcome, isNotNull);
    expect(outcome!.outcomes.single.status, CaptureStatus.remote);
    expect(outcome.outcomes.single.reference, 'server-1');
  });

  test('a shared link is saved locally when there is no network', () async {
    // The share path must not be a way to lose a capture: offline, it lands in
    // the queue exactly like a typed save.
    mockPlatform(initialShare: 'https://example.com/offline-share');
    final ShareIntentService share = ShareIntentService(channel: channel);

    final CaptureBatch? outcome = await share.captureInitialShare(
      captureWith(
        isOnline: () async => false,
        ingest: (String url, _, __) async => accepted('never'),
      ),
    );

    expect(outcome!.outcomes.single.status, CaptureStatus.queued);
    expect(outcome.isQueued, isTrue);
    expect(outcome.outcomes.single.clientId, 'q1');
  });

  test('a share with no link in it is not saved as anything', () async {
    mockPlatform(initialShare: 'just some text, no url here');
    final ShareIntentService share = ShareIntentService(channel: channel);
    int ingests = 0;

    final CaptureBatch? outcome = await share.captureInitialShare(
      captureWith(ingest: (String url, _, __) async {
        ingests++;
        return accepted('server-1');
      }),
    );

    expect(outcome, isNull, reason: 'there was no link to save');
    expect(ingests, 0, reason: 'nothing may be sent to the server');
  });

  test('an app launched normally reads no share', () async {
    mockPlatform();
    final ShareIntentService share = ShareIntentService(channel: channel);
    expect(await share.captureInitialShare(captureWith(
      ingest: (String url, _, __) async => accepted('server-1'),
    )), isNull);
  });

  test('a platform with no share support does not break launching', () async {
    // No handler installed: this is what a unit test, or a platform not yet
    // wired, looks like. Launching must still work.
    final ShareIntentService share = ShareIntentService(channel: channel);
    expect(await share.readInitialShare(), isNull);
  });

  test('a share arriving while the app is open is saved too', () async {
    mockPlatform();
    final ShareIntentService share = ShareIntentService(channel: channel);
    final List<String> saved = <String>[];

    final StreamSubscription<String> listening =
        share.shares.listen((String payload) async {
      await share.captureShared(
        payload,
        captureWith(ingest: (String url, _, __) async {
          saved.add(url);
          return accepted('server-2');
        }),
      );
    });
    addTearDown(listening.cancel);

    // The platform calls `onShare` on the channel.
    await TestDefaultBinaryMessengerBinding
        .instance.defaultBinaryMessenger
        .handlePlatformMessage(
      ShareIntentService.channelName,
      const StandardMethodCodec().encodeMethodCall(
        const MethodCall('onShare', 'https://example.com/live'),
      ),
      (ByteData? _) {},
    );
    await Future<void>.delayed(Duration.zero);

    expect(saved, <String>['https://example.com/live']);
  });

  test('the URL is pulled out of the prose around it', () {
    expect(ShareIntentService.urlFromShare('read this later https://a.test/x'),
        'https://a.test/x');
    expect(ShareIntentService.urlFromShare('no link'), isNull);
  });
}
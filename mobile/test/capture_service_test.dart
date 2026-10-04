import 'package:findback/data/api_client.dart';
import 'package:findback/models/item.dart';
import 'package:findback/services/capture_service.dart';
import 'package:flutter_test/flutter_test.dart';

const IngestResult _ok = IngestResult(id: 'uuid-1', status: 'pending', canonicalUrl: 'https://x/1');

void main() {
  test('online saves go straight to the API and skip the queue', () async {
    final List<String> queued = <String>[];
    final CaptureService service = CaptureService(
      ingest: (String url, String? preview, String? hint) async => _ok,
      queue: (String url, String? preview, String? hint) async {
        queued.add(url);
        return 'client-1';
      },
      isOnline: () async => true,
    );

    final CaptureOutcome outcome = await service.capture(url: 'https://x/1');
    expect(outcome.status, CaptureStatus.remote);
    expect(outcome.reference, 'uuid-1');
    expect(outcome.clientId, isNull);
    expect(queued, isEmpty);
  });

  test('offline saves stay on the device with a predictable id', () async {
    String? queuedUrl;
    final CaptureService service = CaptureService(
      ingest: (String url, String? preview, String? hint) async => _ok,
      queue: (String url, String? preview, String? hint) async {
        queuedUrl = url;
        return 'client-9';
      },
      isOnline: () async => false,
    );

    final CaptureOutcome outcome =
        await service.capture(url: 'https://x/9', preview: 'notes', titleHint: 'Hint');
    expect(outcome.isQueued, isTrue);
    expect(outcome.reference, 'local-client-9');
    expect(outcome.clientId, 'client-9');
    expect(queuedUrl, 'https://x/9');
  });

  test('a dead API is absorbed into the queue, not shown as a failure', () async {
    final CaptureService service = CaptureService(
      ingest: (String url, String? preview, String? hint) async =>
          throw ApiException('connection refused', kind: ApiFailureKind.connectivity),
      queue: (String url, String? preview, String? hint) async => 'client-2',
      isOnline: () async => true,
    );

    final CaptureOutcome outcome = await service.capture(url: 'https://x/2');
    expect(outcome.isQueued, isTrue);
  });

  test('a rejection is the user\'s business, so it is not hidden by the queue', () async {
    final List<String> queued = <String>[];
    final CaptureService service = CaptureService(
      ingest: (String url, String? preview, String? hint) async => throw ApiException(
        'unsupported host',
        statusCode: 422,
        kind: ApiFailureKind.rejected,
      ),
      queue: (String url, String? preview, String? hint) async {
        queued.add(url);
        return 'client-3';
      },
      isOnline: () async => true,
    );

    expect(
      () => service.capture(url: 'not-a-real-page'),
      throwsA(isA<ApiException>()
          .having((ApiException e) => e.statusCode, 'statusCode', 422)),
    );
    expect(queued, isEmpty);
  });

  test('a 503 counts as the server being down, so the capture is queued', () async {
    final CaptureService service = CaptureService(
      ingest: (String url, String? preview, String? hint) async =>
          throw ApiException('bad gateway', statusCode: 503, kind: ApiFailureKind.server),
      queue: (String url, String? preview, String? hint) async => 'client-4',
      isOnline: () async => true,
    );
    expect((await service.capture(url: 'https://x/4')).isQueued, isTrue);
  });
}

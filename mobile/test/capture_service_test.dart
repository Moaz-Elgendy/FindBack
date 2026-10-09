import 'package:findback/data/api_client.dart';
import 'package:findback/models/item.dart';
import 'package:findback/services/capture_service.dart';
import 'package:flutter_test/flutter_test.dart';

const IngestResult _ok = IngestResult(id: 'uuid-1', status: 'pending', canonicalUrl: 'https://x/1');

void main() {
  test('a backend outage retains the capture and is not presented as offline', () async {
    final failure = ApiException('unavailable', statusCode: 503, kind: ApiFailureKind.server);
    final service = CaptureService(ingest: (_, __, ___) async => throw failure,
        queue: (_, __, ___) async => 'pending', isOnline: () async => true);
    final outcome = await service.capture(url: 'https://example.com');
    expect(outcome.isQueued, isTrue);
    expect(outcome.failure, same(failure));
    expect(outcome.queuedMessage, contains('processing service is unavailable'));
    expect(outcome.queuedMessage, isNot(contains('back online')));
  });

  for (final id in ['remote-1', 'local-1']) {
    test('offline repeat of $id is reported without a second memory', () async {
      var queued = 0;
      final service = CaptureService(
        ingest: (_, __, ___) async => throw AssertionError('offline'),
        existingItem: (_) async => ItemDetail.fromJson({'id': id, 'url': 'https://x/1'}),
        queue: (_, __, ___) async { queued++; return '1'; },
        isOnline: () async => false);
      final result = await service.capture(url: 'https://x/1');
      expect(result.alreadyExists, isTrue);
      expect(result.reference, id);
      expect(queued, id.startsWith('local-') ? 1 : 0);
    });
  }

  test('repeat-save signal travels from API to capture outcome', () async {
    final result = IngestResult.fromJson({'id': 'existing', 'status': 'ready',
      'canonical_url': 'https://x/1', 'already_exists': true});
    final service = CaptureService(ingest: (_, __, ___) async => result,
      queue: (_, __, ___) async => throw AssertionError('must not queue'),
      isOnline: () async => true);
    final outcome = await service.capture(url: 'https://x/1');
    expect(outcome.alreadyExists, isTrue);
    expect(outcome.reference, 'existing');
    expect(_ok.alreadyExists, isFalse);
  });

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

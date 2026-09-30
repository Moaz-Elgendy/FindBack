import '../data/api_client.dart';
import '../data/local_db.dart';
import '../models/item.dart';
import 'sync_service.dart' show ConnectivityProbe, systemIsOnline;

typedef IngestUrl = Future<IngestResult> Function(String url, String? preview, String? titleHint);
typedef QueueSave = Future<String> Function(String url, String? preview, String? titleHint);

enum CaptureStatus {
  /// The API accepted it; enrichment runs server-side.
  remote,

  /// Stored on the device and queued; nothing reached the server yet.
  queued,
}

class CaptureOutcome {
  const CaptureOutcome({required this.status, required this.reference, this.clientId});

  final CaptureStatus status;

  /// Server item id when [status] is `remote`, otherwise the local row id.
  final String reference;

  /// Queue row id, present only for a queued capture.
  final String? clientId;

  bool get isQueued => status == CaptureStatus.queued;
}

/// The Save path: try the API, and if the network is the reason it failed, keep
/// the capture on-device so the user never loses a link.
class CaptureService {
  CaptureService({
    required IngestUrl ingest,
    required QueueSave queue,
    ConnectivityProbe? isOnline,
  })  : _ingest = ingest,
        _queue = queue,
        _isOnline = isOnline ?? systemIsOnline;

  factory CaptureService.of({required ApiClient api, required LocalDb db, ConnectivityProbe? isOnline}) =>
      CaptureService(
        ingest: (String url, String? preview, String? titleHint) =>
            api.ingestUrl(url, preview: preview, titleHint: titleHint),
        queue: (String url, String? preview, String? titleHint) =>
            db.queueSave(url: url, preview: preview, titleHint: titleHint),
        isOnline: isOnline,
      );

  final IngestUrl _ingest;
  final QueueSave _queue;
  final ConnectivityProbe _isOnline;

  /// Throws [ApiException] when the server actively rejects the URL — that is a
  /// real answer the user should see. Only connectivity problems are absorbed
  /// into the queue.
  Future<CaptureOutcome> capture({required String url, String? preview, String? titleHint}) async {
    if (await _isOnline()) {
      try {
        final IngestResult result = await _ingest(url, preview, titleHint);
        return CaptureOutcome(status: CaptureStatus.remote, reference: result.id);
      } on ApiException catch (error) {
        if (!error.isRetryableOffline) rethrow;
      }
    }
    final String clientId = await _queue(url, preview, titleHint);
    return CaptureOutcome(
      status: CaptureStatus.queued,
      reference: 'local-$clientId',
      clientId: clientId,
    );
  }
}

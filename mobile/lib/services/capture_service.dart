import '../data/api_client.dart';
import '../utils/share_text.dart';
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
  const CaptureOutcome({required this.status, required this.reference, this.clientId, this.alreadyExists = false, this.failure});

  final CaptureStatus status;
  final bool alreadyExists;
  final ApiException? failure;
  String get queuedMessage => failure?.queuedMessage ?? 'Saved on this phone. Will be read when you’re back online.';

  /// Server item id when [status] is `remote`, otherwise the local row id.
  final String reference;

  /// Queue row id, present only for a queued capture.
  final String? clientId;

  bool get isQueued => status == CaptureStatus.queued;
}

class CaptureBatch {
  const CaptureBatch(this.outcomes, {this.failedUrls = const []});
  final List<CaptureOutcome> outcomes;
  final List<String> failedUrls;
  bool get isQueued => outcomes.any((outcome) => outcome.isQueued);
}

/// The Save path: try the API, and if the network is the reason it failed, keep
/// the capture on-device so the user never loses a link.
class CaptureService {
  CaptureService({
    required IngestUrl ingest,
    required QueueSave queue,
    ConnectivityProbe? isOnline,
    this.onRetryableFailure,
    this.canUpload,
    Future<ItemDetail?> Function(String url)? existingItem,
  })  : _ingest = ingest,
        _queue = queue,
        _isOnline = isOnline ?? systemIsOnline,
        _existingItem = existingItem;

  factory CaptureService.of({required ApiClient api, required LocalDb db, ConnectivityProbe? isOnline, void Function(ApiException)? onRetryableFailure, bool Function()? canUpload}) =>
      CaptureService(
        onRetryableFailure: onRetryableFailure,
        canUpload: canUpload,
      ingest: (String url, String? preview, String? titleHint) =>
            api.ingestUrl(url, preview: preview, titleHint: titleHint),
        queue: (String url, String? preview, String? titleHint) =>
            db.queueSave(url: url, preview: preview, titleHint: titleHint),
        isOnline: isOnline,
        existingItem: db.localItemForUrl,
      );

  final bool Function()? canUpload;
  final void Function(ApiException)? onRetryableFailure;
  final IngestUrl _ingest;
  final QueueSave _queue;
  final ConnectivityProbe _isOnline;
  final Future<ItemDetail?> Function(String url)? _existingItem;

  Future<CaptureBatch> captureText(String text, {String? titleHint}) async {
    final urls = extractUrlsFromShareText(text);
    final outcomes = <CaptureOutcome>[];
    final failed = <String>[];
    for (final url in urls) {
      try {
        outcomes.add(await capture(url: url,
            preview: previewFromText(urls.length == 1 ? text : url),
            titleHint: titleHint, queueOnly: urls.length > 1));
      } catch (_) {
        failed.add(url);
      }
    }
    return CaptureBatch(outcomes, failedUrls: failed);
  }

  /// Throws [ApiException] when the server actively rejects the URL — that is a
  /// real answer the user should see. Only connectivity problems are absorbed
  /// into the queue.
  Future<CaptureOutcome> capture({required String url, String? preview, String? titleHint, bool queueOnly = false}) async {
    ApiException? failure;
    if (!queueOnly && (canUpload?.call() ?? true) && await _isOnline()) {
      try {
        final IngestResult result = await _ingest(url, preview, titleHint);
        return CaptureOutcome(status: CaptureStatus.remote, reference: result.id, alreadyExists: result.alreadyExists);
      } on ApiException catch (error) {
        if (!error.isRetryableOffline) rethrow;
        failure = error;
        onRetryableFailure?.call(error);
      }
    }
    final existing = await _existingItem?.call(url);
    if (existing != null && !existing.id.startsWith('local-')) {
      return CaptureOutcome(status: CaptureStatus.remote, reference: existing.id, alreadyExists: true);
    }
    final String clientId = await _queue(url, preview, titleHint);
    return CaptureOutcome(
      status: CaptureStatus.queued,
      reference: 'local-$clientId',
      clientId: clientId,
      failure: failure,
      alreadyExists: existing != null,
    );
  }
}

import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/models/item.dart';
import 'package:findback/services/guest_library.dart';
import 'package:findback/services/memory_actions.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

class _Api extends ApiClient {
  final deleted = <String>[];
  @override
  Future<IngestResult> ingestUrl(String url, {String? preview, String? titleHint}) async =>
    IngestResult(id: 'fresh', status: 'pending', canonicalUrl: url);
  @override
  Future<void> deleteItem(String id) async => deleted.add(id);
}

void main() {
  setUpAll(() { sqfliteFfiInit(); databaseFactory = databaseFactoryFfi; });
  ItemDetail item(String id, {bool edited = false, String status = 'ready', String brief = 'Previous brief'}) => ItemDetail.fromJson({
    'id': id, 'url': 'https://example.test/old', 'title_clean': 'Old title', 'status': status,
    'instant_brief': brief, 'brief_source': 'llm', 'edited': edited,
  });
  test('an edited empty brief is not treated as unfinished processing', () {
    final edited = ItemDetail.fromJson({'id': 'edited', 'url': 'https://example.test',
      'status': 'ready', 'brief_source': 'llm', 'instant_brief': '', 'edited': true});
    expect(edited.briefText, isEmpty);
    expect(edited.isGeneratingBrief, isFalse);
    expect(ItemDetail.fromLocalRow(edited.toLocalRow()).isGeneratingBrief, isFalse);
  });
  test('automatic guest refresh preserves edits', () async {
    final db = await LocalDb.openAt(inMemoryDatabasePath); final api = _Api();
    await db.upsertRemoteItems([item('old', edited: true)]);
    await GuestLibrary(db, api).cacheAndRelease(item('old', brief: 'Automatic overwrite'));
    expect((await db.localItem('old'))!.briefText, 'Previous brief');
    expect((await db.localItem('old'))!.edited, isTrue);
    await db.close(); api.close();
  });
  for (final succeeded in [false, true]) {
    test('guest explicit replacement ${succeeded ? 'succeeds' : 'fails'} safely', () async {
      final db = await LocalDb.openAt(inMemoryDatabasePath); final api = _Api();
      final guest = GuestLibrary(db, api);
      final sync = SyncService(pending: () async => [], send: (_) async => const SyncBatchResult(mapped: [], failedClientIds: []),
        apply: (_) async {}, markFailed: (_) async {}, isOnline: () async => false);
      await db.upsertRemoteItems([item('old', edited: true)]);
      final actions = MemoryActions(db: db, api: api, sync: sync, guestLibrary: guest);
      final reading = await actions.summarizeAgain('old', replaceEdits: true);
      expect(reading.reprocessing, isTrue);
      expect(reading.briefText, 'Previous brief');
      await guest.cacheAndRelease(item('fresh', status: succeeded ? 'ready' : 'failed', brief: succeeded ? 'New brief' : ''));
      final result = (await db.localItem('fresh'))!;
      expect(result.briefText, succeeded ? 'New brief' : 'Previous brief');
      expect(result.edited, !succeeded);
      expect(result.reprocessing, isFalse);
      expect(result.reprocessFailure, succeeded ? isNull : isNotNull);
      expect(api.deleted, ['fresh']);
      await sync.stop(); await db.close(); api.close();
    });
  }
}

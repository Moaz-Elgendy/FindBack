import 'package:findback/data/api_client.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/search_result.dart';
import 'package:findback/services/items_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter_test/flutter_test.dart';

ItemDetail _item(String id, {String title = 'Server'}) => ItemDetail.fromJson(
    <String, Object?>{'id': id, 'url': 'https://example.com/$id', 'title_clean': '$title $id'});

class _Harness {
  final List<String> deleted = <String>[];
  final List<String> dropped = <String>[];
  final Map<String, ItemDetail> cached = <String, ItemDetail>{};
  final Map<String, ItemDetail> local = <String, ItemDetail>{};
  int remoteCalls = 0;

  ItemsService service({
    bool online = true,
    Future<ItemDetail> Function(String id)? remoteItem,
    RemoteRecentFetch? remoteRecent,
    Future<void> Function(String id)? remoteDelete,
    List<SearchResult>? localRecent,
    RemoteOpen? markRemoteOpened,
    OpenQueuer? queueOpen,
  }) =>
      ItemsService(
        remoteItem: remoteItem ??
            (String id) async {
              remoteCalls++;
              return _item(id);
            },
        localItem: (String id) async => local[id],
        remoteRecent: remoteRecent ??
            (int limit, {String? category, String? cursor, Map<String, String>? filters}) async {
              remoteCalls++;
              return ItemPage(items: <ItemDetail>[_item('r1')]);
            },
        localRecent: (int limit, {String? category, Map<String, String>? filters}) async =>
            localRecent ?? local.values.map(SearchResult.fromItem).toList(),
        cache: (List<ItemDetail> items) async {
          for (final ItemDetail item in items) {
            cached[item.id] = item;
          }
        },
        remoteDelete: remoteDelete ?? (String id) async {},
        localDelete: (String id) async {
          deleted.add(id);
          return 1;
        },
        dropQueued: (String clientId) async {
          dropped.add(clientId);
          return 1;
        },
        isOnline: () async => online,
        markRemoteOpened: markRemoteOpened,
        queueOpen: queueOpen,
      );
}

void main() {
  test('library pages preserve cursor, category and cached Brief', () async {
    final h = _Harness();
    final asked = <String?>[];
    final service = h.service(remoteRecent: (limit, {String? category, String? cursor, Map<String, String>? filters}) async {
      expect(category, 'tutorial');
      asked.add(cursor);
      return ItemPage(items: [_item(cursor == null ? 'first' : 'older')],
          nextCursor: cursor == null ? 'next' : null);
    });
    final first = await service.recentPage(category: 'tutorial');
    final second = await service.recentPage(category: 'tutorial', cursor: first.nextCursor);
    expect(asked, [null, 'next']);
    expect(second.items.single.id, 'older');
    expect(second.nextCursor, isNull);
    expect(h.cached.keys, ['first', 'older']);
  });

  test('offline library reaches beyond 20 without repeated rows', () async {
    final h = _Harness();
    for (var i = 0; i < 45; i++) { h.local['$i'] = _item('$i'); }
    final service = h.service(online: false);
    final first = await service.recentPage(limit: 20);
    final second = await service.recentPage(limit: 20, cursor: first.nextCursor);
    final third = await service.recentPage(limit: 20, cursor: second.nextCursor);
    expect([...first.items, ...second.items, ...third.items].map((r) => r.id),
        List.generate(45, (i) => '$i'));
    expect(third.nextCursor, isNull);
  });

  test('an unsaved item never asks the API', () async {
    final _Harness h = _Harness()..local['local-7'] = _item('local-7', title: 'Offline');
    final ItemDetail? item = await h.service(online: true).getItem('local-7');
    expect(item!.bestTitle, 'Offline local-7');
    expect(h.remoteCalls, 0);
  });

  test('a fetched item leaves a readable copy behind', () async {
    final _Harness h = _Harness();
    await h.service().getItem('uuid-1');
    expect(h.cached.keys, <String>['uuid-1']);
  });

  test('a dead network serves the cached copy', () async {
    final _Harness h = _Harness()..local['uuid-2'] = _item('uuid-2', title: 'Cached');
    final ItemDetail? item = await h.service(
      remoteItem: (String id) async => throw ApiException('offline'),
    ).getItem('uuid-2');
    expect(item!.bestTitle, 'Cached uuid-2');
  });

  test('a permission error is reported instead of a stale lie', () async {
    final _Harness h = _Harness()..local['uuid-3'] = _item('uuid-3');
    expect(
      () => h.service(
        remoteItem: (String id) async =>
            throw ApiException('forbidden', statusCode: 403, kind: ApiFailureKind.unauthorized),
      ).getItem('uuid-3'),
      throwsA(isA<ApiException>()),
    );
  });

  test('the library list falls back to what is on the device', () async {
    final _Harness h = _Harness()
      ..local['local-1'] = _item('local-1');
    final List<SearchResult> rows =
        await h.service(online: false, remoteRecent: (int limit, {String? category, String? cursor, Map<String, String>? filters}) async => throw AssertionError()).recent();
    expect(rows.single.title, 'Server local-1');
    expect(h.remoteCalls, 0);
  });

  test('deleting an unsaved item also cancels its queued upload', () async {
    final _Harness h = _Harness();
    final DeleteOutcome outcome = await h.service().remove('local-client-9');
    expect(outcome.status, DeleteStatus.deleted);
    expect(h.deleted, <String>['local-client-9']);
    expect(h.dropped, <String>['client-9']);
  });

  test('offline deletes hide the item and promise a later sync', () async {
    final _Harness h = _Harness();
    final DeleteOutcome outcome =
        await h.service(online: false, remoteDelete: (String id) async => throw AssertionError())
            .remove('uuid-5');
    expect(outcome.needsNetwork, isTrue);
    expect(h.deleted, <String>['uuid-5']);
  });

  test('a server-side 404 counts as deleted', () async {
    final _Harness h = _Harness();
    final DeleteOutcome outcome = await h.service(
      remoteDelete: (String id) async =>
          throw ApiException('gone', statusCode: 404, kind: ApiFailureKind.rejected),
    ).remove('uuid-6');
    expect(outcome.status, DeleteStatus.deleted);
    expect(h.deleted, <String>['uuid-6']);
  });

  test('a forbidden delete clears the device first, then speaks up', () async {
    final _Harness h = _Harness();
    expect(
      () => h.service(
        remoteDelete: (String id) async =>
            throw ApiException('nope', statusCode: 403, kind: ApiFailureKind.unauthorized),
      ).remove('uuid-7'),
      throwsA(isA<ApiException>()),
    );
    await Future<void>.delayed(const Duration(milliseconds: 5));
    expect(h.deleted, <String>['uuid-7']);
  });

  test('a failed mark-opened call is queued and drains on the next flush', () async {
    final _Harness h = _Harness();
    final List<String> queued = <String>[];
    final ItemsService service = h.service(
      markRemoteOpened: (String id) async => throw ApiException('offline'),
      queueOpen: (String itemId, {required String url}) async => queued.add(itemId),
    );

    // The detail screen opens while the network is down: the call fails, the
    // screen is never told, and the open waits in the queue.
    await service.markOpened(_item('uuid-open-1'));
    expect(queued, <String>['uuid-open-1']);

    // An unsaved local row was never uploaded, so there is nothing to mark.
    await service.markOpened(_item('local-9', title: 'Offline'));
    expect(queued, <String>['uuid-open-1']);

    // Reconnect: the existing sync loop carries the queued open to the server.
    final List<String> sent = <String>[];
    final SyncService sync = SyncService(
      pending: () async => const <SyncItem>[],
      send: (_) async => const SyncBatchResult(mapped: <MappedSave>[], failedClientIds: <String>[]),
      apply: (_) async {},
      markFailed: (_) async {},
      pendingOpens: () async => List<String>.of(queued),
      sendOpen: (String id) async => sent.add(id),
      markOpenDone: (String id) async => queued.remove(id),
      isOnline: () async => true,
    );
    addTearDown(sync.stop);

    await sync.flush();
    expect(sent, <String>['uuid-open-1']);
    expect(queued, isEmpty, reason: 'a delivered open must leave the queue');
  });
}

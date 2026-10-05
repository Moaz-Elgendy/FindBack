import 'package:findback/data/api_client.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/search_result.dart';
import 'package:findback/services/items_service.dart';
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
  }) =>
      ItemsService(
        remoteItem: remoteItem ??
            (String id) async {
              remoteCalls++;
              return _item(id);
            },
        localItem: (String id) async => local[id],
        remoteRecent: remoteRecent ??
            (int limit, {String? category, String? cursor}) async {
              remoteCalls++;
              return ItemPage(items: <ItemDetail>[_item('r1')]);
            },
        localRecent: (int limit, {String? category}) async =>
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
      );
}

void main() {
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
        await h.service(online: false, remoteRecent: (int limit, {String? category, String? cursor}) async => throw AssertionError()).recent();
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
}

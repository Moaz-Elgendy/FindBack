import 'package:findback/data/api_client.dart';
import 'package:findback/features/account/share_settings.dart';
import 'package:findback/models/item.dart';
import 'package:findback/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class SharingApi extends ApiClient {
  String? name;
  final revoked = <String>[];
  @override
  Future<Map<String, dynamic>> shareProfile() async => {'display_name': 'Alex'};
  @override
  Future<List<Map<String, dynamic>>> activeShares() async => [
    {'id': 'share-1', 'title': 'A shared memory', 'expires_at': '2030-01-01T12:00:00Z'},
  ];
  @override
  Future<void> saveDisplayName(String? value) async { name = value; }
  @override
  Future<void> revokeShare(String id) async { revoked.add(id); }
}

void main() {
  test('attribution survives the existing SQLite payload without schema changes', () {
    final item = ItemDetail.fromJson({
      'id': 'copy', 'url': 'https://example.test', 'title': 'Shared title',
      'shared_by': 'Alex', 'status': 'ready', 'key_points': [],
      'brief_source': 'shared_snapshot', 'instant_brief': 'A useful brief',
    });
    final cached = ItemDetail.fromLocalRow(item.toLocalRow());
    expect(cached.sharedBy, 'Alex');
    expect(cached.briefSource, 'shared_snapshot');
    expect(cached.isGeneratingBrief, isFalse);
  });
  testWidgets('sender can save a name and revoke a link', (tester) async {
    final api = SharingApi();
    await tester.pumpWidget(MaterialApp(theme: FindBackTheme.build(Brightness.light),
      home: Scaffold(body: SingleChildScrollView(child: ShareSettings(api: api)))));
    await tester.pumpAndSettle();
    expect(find.text('Alex'), findsOneWidget);
    await tester.enterText(find.byType(TextField), 'Sam');
    await tester.tap(find.text('Save name'));
    await tester.pumpAndSettle();
    expect(api.name, 'Sam');
    await tester.ensureVisible(find.text('Revoke'));
    await tester.tap(find.text('Revoke'));
    await tester.pumpAndSettle();
    expect(api.revoked, ['share-1']);
    expect(find.text('A shared memory'), findsNothing);
    expect(find.textContaining('never affect copies'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    api.close();
  });
}

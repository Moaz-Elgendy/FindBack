import 'dart:io';
import 'dart:convert';
import 'package:findback/app_services.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/data/token_store.dart';
import 'package:findback/services/account_coordinator.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/items_service.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

class LimitedGuestApi extends ApiClient {
  int calls = 0;
  bool validLegacy = false;
  @override
  Future<Map<String, dynamic>> validateGuestSession(String token) async {
    if (validLegacy) return {'is_guest': true};
    throw ApiException('Foreign lease', kind: ApiFailureKind.unauthorized, statusCode: 401);
  }

  @override
  Future<Map<String, dynamic>> createGuestSession() async {
    calls++;
    throw ApiException('Please try later.', statusCode: 429,
        kind: ApiFailureKind.rejected, retryAfterSeconds: 3600);
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  sqfliteFfiInit();
  databaseFactory = databaseFactoryFfi;

  for (final validLegacy in [false, true]) {
  test('guest-session migration validates legacy lease ($validLegacy)', () async {
    FlutterSecureStorage.setMockInitialValues({
      'findback.guestScope': 'retryguest',
      'findback.guestToken.retryguest': jsonEncode({
        'access_token': 'lease-from-another-backend',
        'expires_at': DateTime.now().add(const Duration(days: 1)).toIso8601String(),
      }),
    });
    final directory = await Directory.systemTemp.createTemp('findback-guest-retry');
    final api = LimitedGuestApi()..validLegacy = validLegacy;
    TokenStore? tokens;
    final coordinator = await AccountCoordinator.create(guestApi: api,
        factory: (scope, guest, boundTokens) async {
      tokens = boundTokens;
      final db = await LocalDb.openAt('${directory.path}/guest.db');
      return AppServices(db: db, api: api, guest: true,
          items: ItemsService.of(db: db, api: api), share: ShareIntentService(),
          capture: CaptureService.of(db: db, api: api),
          sync: SyncService.of(db: db, api: api));
    });
    try {
      for (var i = 0; i < 3; i++) {
        if (validLegacy) {
          expect(await tokens!.read(), 'lease-from-another-backend');
          continue;
        }
        await expectLater(tokens!.read(), throwsA(isA<ApiException>()
            .having((e) => e.statusCode, 'status', 429)));
      }
      expect(api.calls, validLegacy ? 0 : 1);
    } finally {
      await coordinator.close();
      await directory.delete(recursive: true);
    }
  });
  }
}

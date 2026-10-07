import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:findback/services/auth_service.dart';
import 'package:findback/features/account/account_page.dart';

Map<String, dynamic> tokens({int expires = 3600, String id = 'user-a'}) => {
      'access_token': 'access',
      'refresh_token': 'refresh',
      'expires_in': expires,
      'user': {'id': id, 'email': 'test@example.com'}
    };
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late Dio dio;
  late AuthService auth;
  late List<RequestOptions> requests;
  late Map<String, dynamic> response;
  setUp(() {
    FlutterSecureStorage.setMockInitialValues({});
    requests = [];
    response = tokens();
    dio = Dio()
      ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
        requests.add(request);
        handler.resolve(
            Response(requestOptions: request, data: response, statusCode: 200));
      }));
    auth = AuthService(
        dio: dio,
        supabaseUrl: 'https://project.supabase.co',
        publicKey: 'public');
  });
  test(
      'normalize email, preserve password, securely restore, bound subject, logout',
      () async {
    await auth.signIn(' TEST@example.com ', ' pass ');
    expect((requests.single.data as Map)['password'], ' pass ');
    expect((requests.single.data as Map)['email'], 'test@example.com');
    expect(await const FlutterSecureStorage().read(key: 'findback.authSession'),
        contains('refresh'));
    final restored = AuthService(
        dio: dio,
        supabaseUrl: 'https://project.supabase.co',
        publicKey: 'public');
    final count = requests.length;
    await restored.restore();
    expect(restored.currentSession!.id, 'user-a');
    expect(requests.length, count);
    expect(await auth.accessTokenFor('other-user'), isNull);
    await auth.signOut();
    expect(auth.currentSession, isNull);
    expect(await const FlutterSecureStorage().read(key: 'findback.authSession'),
        isNull);
  });
  test('confirmation stays guest and existing email suggests sign in/reset',
      () async {
    response = {
      'user': {
        'id': 'new',
        'email': 'test@example.com',
        'identities': [
          {'id': 'new'}
        ]
      }
    };
    expect(
        await auth.signUp('test@example.com', '123456'), contains('confirm'));
    expect(auth.currentSession, isNull);
    response = {
      'user': {'identities': []}
    };
    expect(
        await auth.signUp('test@example.com', '123456'), contains('already'));
    expect(auth.currentSession, isNull);
  });
  test('expired refresh rejects provider account switch', () async {
    response = tokens(expires: -1);
    await auth.signIn('test@example.com', '123456');
    response = tokens();
    expect(await auth.accessTokenFor('user-a'), 'access');
    expect(requests.last.queryParameters['grant_type'], 'refresh_token');
    response = tokens(expires: -1);
    await auth.signIn('test@example.com', '123456');
    response = tokens(id: 'other');
    expect(await auth.accessTokenFor('user-a'), isNull);
    expect(auth.currentSession, isNull);
  });
  test('recovery origin and provider verification before adopting', () async {
    expect(
        await auth.handleRecoveryLink(
            'https://evil.test/auth/recovery#access_token=a'),
        false);
    expect(requests, isEmpty);
    response = {'id': 'verified', 'email': 'test@example.com'};
    expect(
        await auth.handleRecoveryLink(
            'findback://auth/recovery#access_token=a&refresh_token=r&type=recovery'),
        true);
    expect(requests.single.path, endsWith('/user'));
    expect(requests.single.headers['Authorization'], 'Bearer a');
    expect(auth.currentSession!.id, 'verified');
    await auth.updatePassword('newpass');
    expect(requests.last.method, 'PUT');
    expect(requests.last.data, {'password': 'newpass'});
  });
  test('validation and reset redirect', () async {
    expect(
        () => AuthService.validateEmail('bad'), throwsA(isA<AuthException>()));
    expect(() => AuthService.validatePassword('12345'),
        throwsA(isA<AuthException>()));
    await auth.sendPasswordReset('TEST@example.com');
    expect(requests.single.queryParameters['redirect_to'],
        'findback://auth/recovery');
  });
  test('wrong credentials have a helpful message and remain guest', () async {
    dio.interceptors.clear();
    dio.interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
      handler.reject(DioException(
          requestOptions: request,
          response: Response(requestOptions: request, statusCode: 400),
          type: DioExceptionType.badResponse));
    }));
    await expectLater(
        auth.signIn('test@example.com', '123456'),
        throwsA(isA<AuthException>()
            .having((e) => e.message, 'message', contains('password reset'))));
    expect(auth.currentSession, isNull);
  });
  test('logout during in-flight sign in cannot restore credentials', () async {
    dio.interceptors.clear();
    dio.interceptors
        .add(InterceptorsWrapper(onRequest: (request, handler) async {
      await auth.signOut();
      handler.resolve(
          Response(requestOptions: request, data: tokens(), statusCode: 200));
    }));
    await auth.signIn('test@example.com', '123456');
    expect(auth.currentSession, isNull);
    expect(await const FlutterSecureStorage().read(key: 'findback.authSession'),
        isNull);
  });
  test('temporary refresh network failure preserves cached account session',
      () async {
    response = tokens(expires: -1);
    await auth.signIn('test@example.com', '123456');
    dio.interceptors.clear();
    dio.interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
      handler.reject(DioException(
          requestOptions: request, type: DioExceptionType.connectionError));
    }));
    await expectLater(
        auth.accessTokenFor('user-a'),
        throwsA(isA<AuthException>()
            .having((e) => e.retryable, 'retryable', true)));
    expect(auth.currentSession!.id, 'user-a');
    expect(await const FlutterSecureStorage().read(key: 'findback.authSession'),
        isNotNull);
  });
  test('dispose preserves the securely stored session', () async {
    await auth.signIn('test@example.com', '123456');
    final stored = await const FlutterSecureStorage().read(key: 'findback.authSession');
    await auth.dispose();
    expect(await const FlutterSecureStorage().read(key: 'findback.authSession'), stored);
  });
  test('pause delivery and repeated disposal preserve replacement listener', () async {
    const channel = MethodChannel(ShareIntentService.channelName);
    final messenger = TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    messenger.setMockMethodCallHandler(channel, (call) async {
      expect(call.method, 'pauseDelivery'); return null;
    });
    final previous = ShareIntentService(channel: channel);
    await previous.pauseDelivery();
    await previous.dispose();
    final replacement = ShareIntentService(channel: channel);
    await previous.dispose();
    final received = replacement.authLinks.first;
    await messenger.handlePlatformMessage(channel.name,
      const StandardMethodCodec().encodeMethodCall(const MethodCall('onAuthLink', 'replacement')),
      (_) {});
    expect(await received, 'replacement');
    await replacement.dispose();
    messenger.setMockMethodCallHandler(channel, null);
  });
  test('auth links use separate platform method and stream', () async {
    const channel = MethodChannel(ShareIntentService.channelName);
    final messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    messenger.setMockMethodCallHandler(channel, (call) async {
      expect(call.method, 'getInitialAuthLink');
      return 'findback://auth/recovery#type=recovery';
    });
    final service = ShareIntentService(channel: channel);
    expect(await service.readInitialAuthLink(),
        startsWith('findback://auth/recovery'));
    final received = service.authLinks.first;
    await messenger.handlePlatformMessage(
        channel.name,
        const StandardMethodCodec()
            .encodeMethodCall(const MethodCall('onAuthLink', 'warm-recovery')),
        (_) {});
    expect(await received, 'warm-recovery');
    final closed = service.authLinks.drain<void>();
    await service.dispose();
    await closed;
    messenger.setMockMethodCallHandler(channel, null);
  });
  testWidgets('guest account form validates fields', (tester) async {
    await tester.pumpWidget(MaterialApp(home: AccountPage(auth: auth)));
    expect(find.textContaining('guest'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Sign in'));
    await tester.pump();
    expect(find.text('Enter a valid email address.'), findsOneWidget);
    expect(
        find.text('Password must have at least 6 characters.'), findsOneWidget);
    expect(requests, isEmpty);
  });
  test('paused share listener cannot hold account disposal open', () async {
    final service = ShareIntentService();
    final listener = service.shares.listen((_) {});
    listener.pause();
    await service.dispose().timeout(const Duration(seconds: 1));
    await listener.cancel();
  });

}

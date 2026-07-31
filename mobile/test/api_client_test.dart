import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:schapp/src/api/api_error.dart';
import 'package:schapp/src/api/cache.dart';
import 'package:schapp/src/api/client.dart';
import 'package:schapp/src/api/repository.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Every transport failure — DNS, TLS, timeout, dropped socket — arrives as
/// this, and the client must read all of them as "offline".
http.Client _dead() =>
    MockClient((_) async => throw http.ClientException('down'));

/// Swallows a rejected request in a `Future.wait` without leaving the error
/// unhandled — the test is asserting on the callback, not on the throw.
const nothing = ApiResponse(null);

void main() {
  setUp(() {
    TestWidgetsFlutterBinding.ensureInitialized();
    SharedPreferences.setMockInitialValues({});
  });

  Future<ApiClient> clientWith(http.Client http) async {
    final store = OfflineStore(await SharedPreferences.getInstance());
    return ApiClient(store: store, httpClient: http)
      ..tenantSlug = 'kings-college';
  }

  test(
    'the error envelope becomes an ApiError with the machine code',
    () async {
      final api = await clientWith(
        MockClient(
          (_) async => http.Response(
            jsonEncode({
              'error': {
                'code': 'OTP_RATE_LIMITED',
                'message': 'Too many requests.',
                'details': {'retry_after': 60},
              },
            }),
            429,
            headers: {'retry-after': '60'},
          ),
        ),
      );

      await expectLater(
        api.post('/auth/otp/request/'),
        throwsA(
          isA<ApiError>()
              .having((e) => e.code, 'code', 'OTP_RATE_LIMITED')
              .having(
                (e) => e.retryAfter,
                'retryAfter',
                const Duration(seconds: 60),
              ),
        ),
      );
    },
  );

  test('a non-envelope body still yields an ApiError, not a crash', () async {
    final api = await clientWith(
      MockClient(
        (_) async => http.Response('<html>502 Bad Gateway</html>', 502),
      ),
    );
    await expectLater(
      api.get('/auth/me/'),
      throwsA(isA<ApiError>().having((e) => e.status, 'status', 502)),
    );
  });

  test('an unreachable server serves the last cached GET', () async {
    final prefs = await SharedPreferences.getInstance();
    final store = OfflineStore(prefs);

    final online = ApiClient(
      store: store,
      httpClient: MockClient(
        (_) async => http.Response('{"full_name":"Ada Obi"}', 200),
      ),
    )..tenantSlug = 'kings-college';
    await online.get('/auth/me/');

    final offline = ApiClient(store: store, httpClient: _dead())
      ..tenantSlug = 'kings-college';
    final response = await offline.get('/auth/me/');

    expect(response.fromCache, isTrue);
    expect(response.data['full_name'], 'Ada Obi');
    expect(offline.offline.value, isTrue);
  });

  test(
    'offline with nothing cached throws rather than inventing data',
    () async {
      final api = await clientWith(_dead());
      await expectLater(
        api.get('/auth/me/'),
        throwsA(isA<ApiError>().having((e) => e.isOffline, 'isOffline', true)),
      );
    },
  );

  test('another tenant does not read this tenant cached response', () async {
    final prefs = await SharedPreferences.getInstance();
    final store = OfflineStore(prefs);

    final kings = ApiClient(
      store: store,
      httpClient: MockClient(
        (_) async => http.Response('{"name":"Kings"}', 200),
      ),
    )..tenantSlug = 'kings-college';
    await kings.get('/academics/terms/');

    final unity = ApiClient(store: store, httpClient: _dead())
      ..tenantSlug = 'unity-poly';
    await expectLater(
      unity.get('/academics/terms/'),
      throwsA(isA<ApiError>().having((e) => e.isOffline, 'isOffline', true)),
    );
  });

  test('a 401 refreshes once, retries, and does not refresh twice', () async {
    var calls = 0;
    var refreshes = 0;
    final api = await clientWith(
      MockClient((request) async {
        calls++;
        final authorized = request.headers['Authorization'] == 'Bearer good';
        return authorized
            ? http.Response('{"ok":true}', 200)
            : http.Response(
                jsonEncode({
                  'error': {'code': 'UNAUTHENTICATED', 'message': 'expired'},
                }),
                401,
              );
      }),
    );
    api
      ..accessToken = 'stale'
      ..onRefreshNeeded = () async {
        refreshes++;
        return 'good';
      };

    final response = await api.get('/auth/me/');

    expect(response.data['ok'], isTrue);
    expect(calls, 2);
    expect(refreshes, 1);
  });

  test('a 401 with the server unreachable still serves the cache', () async {
    // The token aged out and the connection dropped between the two calls, so
    // the 401 comes back but the refresh never lands. Reporting the 401 would
    // hide the saved copy behind "credentials were not provided" — the session
    // is fine, the network is not.
    final store = OfflineStore(await SharedPreferences.getInstance());
    var expired = false;
    final api = ApiClient(
      store: store,
      httpClient: MockClient(
        (_) async => expired
            ? http.Response(
                jsonEncode({
                  'error': {'code': 'UNAUTHENTICATED', 'message': 'expired'},
                }),
                401,
              )
            : http.Response('{"ok":true}', 200),
      ),
    )..tenantSlug = 'kings-college';

    await api.get('/people/staff/'); // fills the cache
    expired = true;
    // What Session.refresh now does when its own POST cannot reach the server.
    api.onRefreshNeeded = () async => throw ApiError.offline();

    final response = await api.get('/people/staff/');

    expect(response.fromCache, isTrue);
    expect(response.data['ok'], isTrue);
  });

  test(
    'a dead refresh surfaces the original 401 for the guard to act on',
    () async {
      final api = await clientWith(
        MockClient(
          (_) async => http.Response(
            jsonEncode({
              'error': {
                'code': 'TOKEN_REUSE_DETECTED',
                'message': 'signed out',
              },
            }),
            401,
          ),
        ),
      );
      api
        ..accessToken = 'stale'
        ..onRefreshNeeded = () async => null;

      await expectLater(
        api.get('/auth/me/'),
        throwsA(
          isA<ApiError>().having(
            (e) => e.requiresSignIn,
            'requiresSignIn',
            true,
          ),
        ),
      );
    },
  );

  test(
    'a 401 on the refresh call itself surfaces instead of deadlocking',
    () async {
      // The refresh runs through this same client, so its own 401 used to
      // re-enter `_refreshOnce` and await the future it was already inside.
      final api = await clientWith(
        MockClient(
          (_) async => http.Response(
            jsonEncode({
              'error': {'code': 'TOKEN_INVALID', 'message': 'Session expired.'},
            }),
            401,
          ),
        ),
      );
      api
        ..accessToken = 'stale'
        ..onRefreshNeeded = () async {
          try {
            await api.post(
              '/auth/token/refresh/',
              body: {'refresh': 'spent'},
              retryOnAuth: false,
            );
          } on ApiError {
            return null; // what Session.refresh does before signing out
          }
          return null;
        };

      await expectLater(
        api.get('/assessment/term-results/'),
        throwsA(
          isA<ApiError>().having(
            (e) => e.requiresSignIn,
            'requiresSignIn',
            true,
          ),
        ),
      );
    },
    timeout: const Timeout(Duration(seconds: 10)),
  );

  test('the refresh call goes out without the expired bearer token', () async {
    // DRF authenticates before it checks AllowAny, so an expired access token
    // on the refresh request is answered 401 TOKEN_NOT_VALID before the view
    // runs — the session can then never recover.
    String? sent = 'unset';
    final api = await clientWith(
      MockClient((request) async {
        sent = request.headers['Authorization'];
        return http.Response(jsonEncode({'access': 'fresh'}), 200);
      }),
    );
    api.accessToken = 'expired';

    await api.post(
      '/auth/token/refresh/',
      body: {'refresh': 'good'},
      retryOnAuth: false,
      auth: false,
    );
    expect(sent, isNull);

    // Every other call still carries it.
    await api.get('/auth/me/');
    expect(sent, 'Bearer expired');
  });

  group('tenant rejection', () {
    http.Client rejecting(String code) => MockClient(
      (_) async => http.Response(
        jsonEncode({
          'error': {'code': code, 'message': 'Not active.'},
        }),
        403,
      ),
    );

    test('a suspended school is reported once, not per screen', () async {
      var rejections = 0;
      final api = await clientWith(rejecting('TENANT_SUSPENDED'));
      api.onTenantRejected = () async {
        rejections++;
        await Future<void>.delayed(const Duration(milliseconds: 20));
      };

      // Two screens loading at once, as the shell does on a cold start.
      await Future.wait([
        api.get('/assessment/term-results/').catchError((_) => nothing),
        api.get('/finance/invoices/').catchError((_) => nothing),
      ]);

      expect(rejections, 1);
    });

    test('the error still reaches the screen that asked for it', () async {
      final api = await clientWith(rejecting('TENANT_MISMATCH'));
      api.onTenantRejected = () async {};

      await expectLater(
        api.get('/assessment/term-results/'),
        throwsA(
          isA<ApiError>().having((e) => e.code, 'code', 'TENANT_MISMATCH'),
        ),
      );
    });

    test(
      'a mistyped school code on the picker does not end a session',
      () async {
        // TENANT_NOT_FOUND on the public lookup is a typo, not a dead school.
        var rejections = 0;
        final prefs = await SharedPreferences.getInstance();
        final api = ApiClient(
          store: OfflineStore(prefs),
          httpClient: MockClient(
            (_) async => http.Response(
              jsonEncode({
                'error': {'code': 'TENANT_NOT_FOUND', 'message': 'No match.'},
              }),
              404,
            ),
          ),
        )..onTenantRejected = () async => rejections++;

        await expectLater(
          api.get('/public/tenants/lookup/', query: {'slug': 'kigns-colege'}),
          throwsA(isA<ApiError>()),
        );
        expect(rejections, 0);
      },
    );
  });

  group('outbox', () {
    test(
      'a queued write is replayed in order when the server returns',
      () async {
        final prefs = await SharedPreferences.getInstance();
        final store = OfflineStore(prefs);

        final offline = ApiClient(store: store, httpClient: _dead())
          ..tenantSlug = 'kings-college';
        final first = await offline.post(
          '/attendance/students/bulk/',
          body: {'day': 1},
          queue: true,
          label: 'Attendance day 1',
        );
        await offline.post(
          '/attendance/students/bulk/',
          body: {'day': 2},
          queue: true,
        );

        expect(first.queued, isTrue);
        expect(await store.pending(), hasLength(2));

        final seen = <int>[];
        final back = ApiClient(
          store: store,
          httpClient: MockClient((request) async {
            seen.add((jsonDecode(request.body) as Map)['day'] as int);
            return http.Response('{}', 201);
          }),
        )..tenantSlug = 'kings-college';

        final result = await back.flushOutbox();

        expect(result.sent, 2);
        expect(seen, [1, 2]);
        expect(await store.pending(), isEmpty);
      },
    );

    test(
      'a write the server rejects is dropped, not retried forever',
      () async {
        final prefs = await SharedPreferences.getInstance();
        final store = OfflineStore(prefs);

        await ApiClient(
          store: store,
          httpClient: _dead(),
        ).post('/assessment/scores/bulk/', body: {'score': 999}, queue: true);

        final back = ApiClient(
          store: store,
          httpClient: MockClient(
            (_) async => http.Response(
              jsonEncode({
                'error': {
                  'code': 'VALIDATION_ERROR',
                  'message': 'Validation failed.',
                },
              }),
              400,
            ),
          ),
        );
        final result = await back.flushOutbox();

        expect(result.rejected, 1);
        expect(await store.pending(), isEmpty);
      },
    );

    test('a 5xx leaves the entry queued for the next attempt', () async {
      final prefs = await SharedPreferences.getInstance();
      final store = OfflineStore(prefs);

      await ApiClient(
        store: store,
        httpClient: _dead(),
      ).post('/assessment/scores/bulk/', body: {'score': 10}, queue: true);

      final back = ApiClient(
        store: store,
        httpClient: MockClient((_) async => http.Response('boom', 503)),
      );
      final result = await back.flushOutbox();

      expect(result.sent, 0);
      expect(await store.pending(), hasLength(1));
    });

    test('without queue: true an offline write fails loudly', () async {
      final api = await clientWith(_dead());
      await expectLater(
        api.post('/auth/pin/set/', body: {'pin': '135790'}),
        throwsA(isA<ApiError>().having((e) => e.isOffline, 'isOffline', true)),
      );
    });
  });

  group('upload', () {
    test('a 422 arrives as the row report, not as an exception', () async {
      // The whole point of the import screen. If this throws, the office sees
      // "something went wrong" instead of the three lines to fix.
      final api = await clientWith(
        MockClient(
          (_) async => http.Response(
            jsonEncode({
              'created': 0,
              'updated': 0,
              'errors': [
                {
                  'index': 3,
                  'identifier': 'Ada Obi',
                  'code': 'BAD_DATE',
                  'message': "'32/13/2020' is not a date.",
                },
              ],
            }),
            422,
          ),
        ),
      );

      final response = await api.upload(
        '/people/students/import/',
        field: 'file',
        filename: 'roster.csv',
        bytes: Uint8List.fromList('first_name,last_name\n'.codeUnits),
      );

      expect(response.data['created'], 0);
      expect(response.data['errors'].first['index'], 3);
    });

    test('the file and the fields both reach the server', () async {
      late String body;
      late String contentType;
      final api = await clientWith(
        MockClient((request) async {
          body = request.body;
          contentType = request.headers['content-type'] ?? '';
          return http.Response('{"created":1,"errors":[]}', 200);
        }),
      );

      await api.upload(
        '/people/students/import/',
        field: 'file',
        filename: 'roster.csv',
        bytes: Uint8List.fromList('first_name\nAda\n'.codeUnits),
        fields: {'enforce_capacity': 'false'},
      );

      expect(contentType, startsWith('multipart/form-data; boundary='));
      expect(body, contains('filename="roster.csv"'));
      expect(body, contains('enforce_capacity'));
    });

    test('a real failure still throws', () async {
      final api = await clientWith(
        MockClient(
          (_) async => http.Response(
            jsonEncode({
              'error': {'code': 'PERMISSION_DENIED', 'message': 'Not allowed.'},
            }),
            403,
          ),
        ),
      );

      await expectLater(
        api.upload(
          '/people/students/import/',
          field: 'file',
          filename: 'roster.csv',
          bytes: Uint8List(0),
        ),
        throwsA(
          isA<ApiError>().having((e) => e.code, 'code', 'PERMISSION_DENIED'),
        ),
      );
    });
  });

  test('download returns the PDF bytes intact', () async {
    // `_send` hands back `response.body`, which mangles binary. This is why
    // the PDF path is separate.
    final pdf = Uint8List.fromList([0x25, 0x50, 0x44, 0x46, 0x2d, 0xff, 0x00]);
    final api = await clientWith(
      MockClient((_) async => http.Response.bytes(pdf, 200)),
    );

    expect(await api.download('/people/students/id-cards/'), pdf);
  });

  test('a role change goes out as PUT, not as a POST', () async {
    // `_dispatch` used to be a two-way branch that fell through to POST, so
    // `put` would have quietly replaced a user's roles by the wrong verb —
    // and `/admin/users/{id}/roles/` answers POST with 405.
    var method = '';
    final api = await clientWith(
      MockClient((request) async {
        method = request.method;
        return http.Response('{"roles":["teacher"]}', 200);
      }),
    );

    await api.put(
      '/admin/users/1/roles/',
      body: {
        'roles': ['teacher'],
      },
    );
    expect(method, 'PUT');
  });

  test('a role change is never queued offline', () async {
    // A stale set replayed out of an outbox would undo whatever was decided
    // in between — the whole point of sending the roles as a whole set.
    final api = await clientWith(_dead());
    await expectLater(
      api.put('/admin/users/1/roles/', body: {'roles': const <String>[]}),
      throwsA(isA<ApiError>().having((e) => e.isOffline, 'isOffline', true)),
    );
    expect(await api.pendingCount, 0);
  });

  test('clear wipes cached rows so the next user cannot read them', () async {
    final prefs = await SharedPreferences.getInstance();
    final store = OfflineStore(prefs);
    final api = ApiClient(
      store: store,
      httpClient: MockClient((_) async => http.Response('{"name":"Ada"}', 200)),
    )..tenantSlug = 'kings-college';

    await api.get('/people/students/');
    await store.clear();

    final offline = ApiClient(store: store, httpClient: _dead())
      ..tenantSlug = 'kings-college';
    await expectLater(
      offline.get('/people/students/'),
      throwsA(isA<ApiError>()),
    );
  });

  test('a queued write replays under the key its first attempt sent', () async {
    // The duplicate this closes: the server commits, the reply is lost on the
    // way back, the client reads that as offline and queues. If the key were
    // minted at queue time the replay would look like a brand new write.
    final prefs = await SharedPreferences.getInstance();
    final store = OfflineStore(prefs);
    final keys = <String?>[];

    final lost = ApiClient(
      store: store,
      httpClient: MockClient((request) async {
        keys.add(request.headers['Idempotency-Key']);
        throw http.ClientException('reply lost');
      }),
    )..tenantSlug = 'kings-college';
    final queued = await lost.post(
      '/attendance/records/',
      body: {'status': 'PRESENT'},
      queue: true,
    );
    expect(queued.queued, isTrue);

    final back = ApiClient(
      store: store,
      httpClient: MockClient((request) async {
        keys.add(request.headers['Idempotency-Key']);
        return http.Response('{"replayed":true}', 201);
      }),
    )..tenantSlug = 'kings-college';
    expect((await back.flushOutbox()).sent, 1);

    expect(keys, hasLength(2));
    expect(keys.first, isNotNull);
    expect(keys.last, keys.first);
  });

  test('an unqueued write sends no key', () async {
    // Nothing to replay it from, so a key would only cost a table row.
    String? seen = 'unset';
    final api = await clientWith(
      MockClient((request) async {
        seen = request.headers['Idempotency-Key'];
        return http.Response('{}', 200);
      }),
    );
    await api.post('/auth/otp/request/');
    expect(seen, isNull);
  });

  test('an in-progress key leaves the entry queued', () async {
    // 409 IDEMPOTENCY_IN_PROGRESS is a 4xx, and every other 4xx means "the
    // server has judged this, drop it". This one means "ask again shortly" —
    // dropping it would discard a write that is about to succeed.
    final prefs = await SharedPreferences.getInstance();
    final store = OfflineStore(prefs);

    final offline = ApiClient(store: store, httpClient: _dead())
      ..tenantSlug = 'kings-college';
    await offline.post('/attendance/records/', queue: true);

    final busy = ApiClient(
      store: store,
      httpClient: MockClient(
        (_) async => http.Response(
          jsonEncode({
            'error': {
              'code': 'IDEMPOTENCY_IN_PROGRESS',
              'message': 'Still processing.',
            },
          }),
          409,
        ),
      ),
    )..tenantSlug = 'kings-college';

    final result = await busy.flushOutbox();
    expect(result.sent, 0);
    expect(result.rejected, 0);
    expect(await busy.pendingCount, 1);
  });

  group('paged lists', () {
    /// A cursor-paginated endpoint, `pages` pages of one row each. `next` is
    /// written absolute against a host this device cannot reach, which is what
    /// a server behind a proxy answers.
    http.Client paged(int pages, {List<Uri>? seen}) => MockClient((
      request,
    ) async {
      seen?.add(request.url);
      final at =
          int.tryParse(request.url.queryParameters['cursor'] ?? '0') ?? 0;
      final more = at + 1 < pages;
      return http.Response(
        jsonEncode({
          'next': more ? 'http://unreachable:8000/x/?cursor=${at + 1}' : null,
          'results': [
            {'id': 'row-$at'},
          ],
        }),
        200,
      );
    });

    test('a list is every page, not the first one', () async {
      // The truncation this closes: an approval queue or a class list one page
      // short looks complete, and the rows past the cap are the ones nobody
      // ever sees to act on.
      final seen = <Uri>[];
      final repo = Repository(await clientWith(paged(3, seen: seen)));

      final rows = await repo.students();

      expect(rows.map((r) => r['id']), ['row-0', 'row-1', 'row-2']);
      expect(seen, hasLength(3));
      // Followed by cursor against our own host, not the one the server named.
      expect(seen.last.host, isNot('unreachable'));
      expect(seen.last.queryParameters['cursor'], '2');
    });

    test('a self-referential next link stops instead of looping', () async {
      final api = await clientWith(
        MockClient(
          (_) async => http.Response(
            jsonEncode({
              'next': 'http://x/y/?cursor=same',
              'results': [
                {'id': 'r'},
              ],
            }),
            200,
          ),
        ),
      );
      expect((await Repository(api).students()).length, 10);
    });

    test('the connection dropping mid-list keeps what was read', () async {
      // One page of a class list beats an error screen. The first page is not
      // forgiven — a screen with nothing to show has to say so, which the
      // caching test above covers.
      final api = await clientWith(
        MockClient((request) async {
          if (request.url.queryParameters.containsKey('cursor')) {
            throw http.ClientException('down');
          }
          return http.Response(
            jsonEncode({
              'next': 'http://x/y/?cursor=1',
              'results': [
                {'id': 'row-0'},
              ],
            }),
            200,
          );
        }),
      );

      expect((await Repository(api).students()).map((r) => r['id']), ['row-0']);
    });
  });
}

import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import '../config.dart';
import 'api_error.dart';
import 'cache.dart';

/// Returns a fresh access token, or null when the session is unrecoverable.
typedef TokenRefresher = Future<String?> Function();

/// Called when the server rejects the *school* rather than the credentials.
typedef TenantRejectedHandler = Future<void> Function();

class ApiResponse {
  const ApiResponse(this.data, {this.cachedAt, this.queued = false});

  final dynamic data;

  /// Non-null when the server was unreachable and this came off the device.
  final DateTime? cachedAt;

  /// The write could not be sent and is sitting in the outbox.
  final bool queued;

  bool get fromCache => cachedAt != null;
}

/// One HTTP client for the whole app: tenant header, bearer token, the error
/// envelope, a single refresh-and-retry on 401, and the offline fallbacks.
class ApiClient {
  ApiClient({required this.store, http.Client? httpClient})
    : _http = httpClient ?? http.Client();

  final OfflineStore store;
  final http.Client _http;

  /// The shell shows a banner off this, so screens do not each handle it.
  final offline = ValueNotifier<bool>(false);

  /// Sent as `X-Tenant-Slug`. The header is what selects the school — the JWT
  /// claim only has to agree with it, so this must be set before any
  /// tenant-scoped call.
  String? tenantSlug;
  String? accessToken;
  TokenRefresher? onRefreshNeeded;

  /// Fired on `TENANT_SUSPENDED`, `TENANT_MISMATCH` and `TENANT_NOT_FOUND`.
  /// Without it a school suspended mid-session leaves every screen showing an
  /// error with no way back to the school picker: the router's guard keys off
  /// the chosen tenant, and nothing else clears it.
  TenantRejectedHandler? onTenantRejected;

  Future<String?>? _refreshing;
  Future<void>? _rejecting;

  Map<String, String> get _headers => _headersFor();

  /// `auth: false` omits the bearer token. Needed by the refresh call: DRF runs
  /// authentication before permissions, so an expired access token in the
  /// header makes `AllowAny` routes answer 401 `TOKEN_NOT_VALID` before the view
  /// ever runs — which is exactly the state a refresh exists to get out of.
  Map<String, String> _headersFor({bool auth = true, String? idempotencyKey}) =>
      {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-Tenant-Slug': ?tenantSlug,
        if (auth && accessToken != null) 'Authorization': 'Bearer $accessToken',
        'Idempotency-Key': ?idempotencyKey,
      };

  Uri _uri(String path, [Map<String, dynamic>? query]) => Uri.parse(
    '${Config.apiRoot}$path',
  ).replace(queryParameters: query?.map((k, v) => MapEntry(k, '$v')));

  // --- verbs ---------------------------------------------------------------

  /// GET, falling back to the cached copy when the server is unreachable.
  ///
  /// Every GET is cached. There used to be a `cache: false` for records whose
  /// staleness would mislead — a payment status, a register somebody else is
  /// editing — but the opt-out never bought that: a cached copy is only ever
  /// *served* when the request failed outright, so online those calls behaved
  /// identically and offline they were the only screens that could not open at
  /// all. Staleness is answered by [ApiResponse.cachedAt], which the shell
  /// renders, not by having nothing to show.
  Future<ApiResponse> get(String path, {Map<String, dynamic>? query}) async {
    final uri = _uri(path, query);
    final key = _cacheKey(uri);
    try {
      final body = await _send(() => _http.get(uri, headers: _headers));
      // An empty body is not cached: the decode below already treats it as
      // null, and the offline branch would have to hand `jsonDecode('')` an
      // empty string and throw `FormatException` — a crash, not a fallback,
      // on the one path that exists to avoid one.
      if (body.isNotEmpty) await store.put(key, body);
      return ApiResponse(body.isEmpty ? null : jsonDecode(body));
    } on ApiError catch (error) {
      if (!error.isOffline) rethrow;
      final cached = await store.get(key);
      if (cached == null || cached.body.isEmpty) rethrow;
      return ApiResponse(jsonDecode(cached.body), cachedAt: cached.at);
    }
  }

  /// Cache keys carry the tenant: one school's rows must never be read back
  /// under another's, and the same path means a different thing per school.
  String _cacheKey(Uri uri) => '${tenantSlug ?? '-'} GET $uri';

  /// POST. With `queue: true` an unreachable server means the write goes to
  /// the outbox instead of failing — use it only where a replay is safe.
  ///
  /// `retryOnAuth: false` is for the refresh call itself: it runs through this
  /// client too, and a 401 there must surface rather than ask for a refresh.
  ///
  /// `allow` lists statuses that come back as *data* rather than an exception.
  /// The bulk endpoints need `{422}`: their per-row report is not the error
  /// envelope, so thrown it degrades to "something went wrong" and the whole
  /// point of the row detail is lost.
  Future<ApiResponse> post(
    String path, {
    Map<String, dynamic> body = const {},
    bool queue = false,
    String label = '',
    bool retryOnAuth = true,
    bool auth = true,
    Set<int> allow = const {},
  }) => _write('POST', path, body, queue, label, retryOnAuth, auth, allow);

  Future<ApiResponse> patch(
    String path, {
    Map<String, dynamic> body = const {},
    bool queue = false,
    String label = '',
  }) => _write('PATCH', path, body, queue, label, true, true);

  /// PUT. The endpoints that take one replace a whole set (a user's roles), so
  /// a replay from the outbox writes the set as it was composed offline over
  /// whatever was decided since — which is why [_maxOutboxAge] exists.
  Future<ApiResponse> put(
    String path, {
    Map<String, dynamic> body = const {},
    bool queue = false,
    String label = '',
  }) => _write('PUT', path, body, queue, label, true, true);

  /// A multipart write — the student CSV import and the student photo.
  /// `method` is PATCH for the second: a photo is one field of a record that
  /// already exists.
  ///
  /// Never queued: an import replayed out of an outbox is a second intake.
  /// A 422 comes back as *data* rather than an error, because the bulk
  /// endpoints answer it with a per-row report and that report is the entire
  /// point of the screen; the transaction rolled back, so nothing was written.
  Future<ApiResponse> upload(
    String path, {
    required String field,
    required String filename,
    required Uint8List bytes,
    Map<String, String> fields = const {},
    String method = 'POST',
  }) async {
    final response = await _sendRaw(
      () async {
        // Rebuilt per attempt: a MultipartRequest cannot be sent twice, and a
        // 401 retry sends it again.
        final request = http.MultipartRequest(method, _uri(path))
          // Content-Type must go: the boundary is the package's to write.
          ..headers.addAll(_headers..remove('Content-Type'))
          ..fields.addAll(fields)
          ..files.add(
            http.MultipartFile.fromBytes(field, bytes, filename: filename),
          );
        return http.Response.fromStream(await _http.send(request));
      },
      allow: const {422},
      timeout: _longRunning,
    );
    return ApiResponse(
      response.body.isEmpty ? null : jsonDecode(response.body),
    );
  }

  /// A binary GET — the printable PDFs — kept on the device so the last few
  /// printed can be printed again with no connection. The store caps both the
  /// count and the size of these, so a four-hundred-page ID-card batch is
  /// fetched and handed to the printer without ever being kept.
  Future<Uint8List> download(String path, {Map<String, dynamic>? query}) async {
    final uri = _uri(path, query);
    final key = _cacheKey(uri);
    try {
      final response = await _sendRaw(
        () => _http.get(uri, headers: {..._headers, 'Accept': 'application/pdf'}),
        timeout: _longRunning,
      );
      if (response.bodyBytes.isNotEmpty) {
        await store.putBytes(key, response.bodyBytes);
      }
      return response.bodyBytes;
    } on ApiError catch (error) {
      if (!error.isOffline) rethrow;
      final cached = await store.getBytes(key);
      if (cached == null) rethrow;
      return cached;
    }
  }

  /// DELETE. Queueable like the rest: the server reads `Idempotency-Key` on it
  /// too, so a replayed removal is the first removal's answer rather than a
  /// 404 the flush would report as a rejection.
  Future<ApiResponse> delete(
    String path, {
    bool queue = false,
    String label = '',
  }) => _write('DELETE', path, const {}, queue, label, true, true);

  Future<ApiResponse> _write(
    String method,
    String path,
    Map<String, dynamic> body,
    bool queue,
    String label,
    bool retryOnAuth,
    bool auth, [
    Set<int> allow = const {},
  ]) async {
    // Minted before the first attempt, not after it fails. A request that
    // times out *after* the server committed looks identical from here to one
    // that never arrived, so the key the outbox replays under has to be the
    // same key that first attempt already carried — otherwise the server sees
    // two unrelated writes and the school gets two of whatever this was.
    final key = queue ? _newId() : null;
    try {
      final response = await _send(
        () => _dispatch(method, path, body, auth, key),
        retryOnAuth: retryOnAuth,
        allow: allow,
      );
      return ApiResponse(response.isEmpty ? null : jsonDecode(response));
    } on ApiError catch (error) {
      if (!error.isOffline || !queue) rethrow;
      await store.enqueue(
        OutboxEntry(
          id: key!,
          method: method,
          path: path,
          body: body,
          at: DateTime.now(),
          label: label,
        ),
      );
      // The write is answered with what it asked for, marked pending. Callers
      // read a record back — `(response.data as Map)['name']` is in half the
      // screens — and handing them null would turn every offline write into a
      // cast error on the line after the one that queued it.
      final record = {...body, 'id': _idIn(path) ?? key, '_pending': true};
      await _applyLocally(method, path, record);
      return ApiResponse(record, queued: true);
    }
  }

  /// The trailing `{id}` of a detail path, or null for a collection. An
  /// optimistic edit has to carry the id it edited so it lands on the right
  /// cached row; only a create invents one, and it borrows the outbox key.
  static String? _idIn(String path) {
    final parts = path.split('/').where((p) => p.isNotEmpty).toList();
    if (parts.length < 2) return null;
    final last = parts.last;
    // `/students/{id}/` ends in the id; `/invoices/{id}/issue/` ends in a verb
    // that names no record, and merging under it would invent a row.
    return RegExp(r'^[0-9a-fA-F-]{6,}$').hasMatch(last) ? last : null;
  }

  /// Fold a queued write into the cached responses, so the list the screen
  /// reloads a second later shows what was just written instead of dropping it
  /// until the network returns.
  ///
  /// ponytail: the REST shapes only — POST to a collection, PATCH/PUT/DELETE on
  /// `/collection/{id}/`. An action endpoint (`/invoices/{id}/issue/`) changes
  /// state this cannot guess, so its row keeps the old status until the flush
  /// lands. Project a specific action here only when one actually misleads.
  Future<void> _applyLocally(
    String method,
    String path,
    Map<String, dynamic> record,
  ) async {
    final target = Uri.parse('${Config.apiRoot}$path').path;
    final isDetail = method != 'POST' && _idIn(path) != null;
    if (!isDetail && method != 'POST') return;
    final collection = isDetail
        ? target.substring(0, target.lastIndexOf('/', target.length - 2) + 1)
        : target;
    final prefix = '${tenantSlug ?? '-'} GET ';

    for (final key in await store.keys()) {
      if (!key.startsWith(prefix)) continue;
      final cachedPath = Uri.tryParse(key.substring(prefix.length))?.path;
      if (cachedPath != collection && !(isDetail && cachedPath == target)) {
        continue;
      }
      final cached = await store.get(key);
      if (cached == null || cached.body.isEmpty) continue;
      dynamic body;
      try {
        body = jsonDecode(cached.body);
      } catch (_) {
        continue;
      }
      final updated = cachedPath == collection
          ? _mergeIntoList(body, method, record)
          : method == 'DELETE' || body is! Map
          ? null // the detail copy of a deleted record is left to expire
          : {...body, ...record};
      if (updated == null) continue;
      await store.put(key, jsonEncode(updated), at: cached.at);
    }
  }

  /// Returns the list response with [record] applied, or null when this cached
  /// body is not a list, or does not hold the row being edited.
  static dynamic _mergeIntoList(
    dynamic body,
    String method,
    Map<String, dynamic> record,
  ) {
    final List<dynamic>? source = body is List
        ? body
        : body is Map && body['results'] is List
        ? body['results'] as List
        : null;
    if (source == null) return null;

    final rows = source.toList();
    final at = rows.indexWhere(
      (row) => row is Map && row['id'] == record['id'],
    );
    switch (method) {
      case 'POST':
        if (at >= 0) return null; // already there; a replay, not a new row
        rows.add(record);
      case 'DELETE':
        if (at < 0) return null;
        rows.removeAt(at);
      default:
        if (at < 0) return null;
        rows[at] = {...(rows[at] as Map), ...record};
    }
    return body is List ? rows : {...(body as Map), 'results': rows};
  }

  Future<http.Response> _dispatch(
    String method,
    String path,
    Map<String, dynamic> body, [
    bool auth = true,
    String? idempotencyKey,
  ]) {
    final uri = _uri(path);
    final encoded = jsonEncode(body);
    final headers = _headersFor(auth: auth, idempotencyKey: idempotencyKey);
    return switch (method) {
      'PATCH' => _http.patch(uri, headers: headers, body: encoded),
      'PUT' => _http.put(uri, headers: headers, body: encoded),
      // Bodyless on purpose: the idempotency fingerprint is method, path and
      // body, and a DELETE that sent `{}` on the first attempt and nothing on
      // the replay would be read as a different request under the same key.
      'DELETE' => _http.delete(uri, headers: headers),
      _ => _http.post(uri, headers: headers, body: encoded),
    };
  }

  // --- outbox --------------------------------------------------------------

  /// Replay queued writes, oldest first. Returns how many were accepted.
  ///
  /// A 4xx that is not an auth problem means the server has judged the write
  /// and will judge it the same way forever, so the entry is dropped rather
  /// than left to poison the queue. Losing connection stops the run and leaves
  /// the rest queued.
  ///
  /// Every replay carries the entry's id as `Idempotency-Key`, which is what
  /// makes this at-least-once loop safe: the server answers a second delivery
  /// with the first one's response instead of writing again. The exception is
  /// 409 `IDEMPOTENCY_IN_PROGRESS` — the original request is still running, so
  /// the entry stays queued for the next flush rather than being dropped as a
  /// judged 4xx.
  /// Now that every write is queueable, the outbox holds things whose meaning
  /// decays: a fee edit, a term rollover, a whole set of roles. Those are safe
  /// to replay in the minutes or hours a phone is normally out of signal, and
  /// not safe to replay against a school that has moved on for a week. An
  /// entry older than this is dropped rather than applied, and reported.
  static const _maxOutboxAge = Duration(days: 7);

  Future<OutboxResult> flushOutbox() async {
    var sent = 0;
    var rejected = 0;
    var expired = 0;
    for (final entry in await store.pending()) {
      if (DateTime.now().difference(entry.at) > _maxOutboxAge) {
        await store.dequeue(entry.id);
        expired++;
        continue;
      }
      try {
        await _send(
          () => _dispatch(entry.method, entry.path, entry.body, true, entry.id),
        );
        await store.dequeue(entry.id);
        sent++;
      } on ApiError catch (error) {
        if (error.isOffline) break;
        if (error.requiresSignIn) break;
        if (error.code == 'IDEMPOTENCY_IN_PROGRESS') break;
        if (error.status >= 400 && error.status < 500) {
          await store.dequeue(entry.id);
          rejected++;
          continue;
        }
        break; // 5xx: the server's problem, try again later
      }
    }
    return OutboxResult(sent: sent, rejected: rejected, expired: expired);
  }

  Future<int> get pendingCount async => (await store.pending()).length;

  // --- transport -----------------------------------------------------------

  /// Twenty seconds is right for a screen waiting on a list. Uploading four
  /// hundred pupils, or rendering four hundred ID cards, is a job the server
  /// does in one transaction and the office expects to wait for.
  static const _longRunning = Duration(seconds: 120);

  Future<String> _send(
    Future<http.Response> Function() send, {
    bool retryOnAuth = true,
    Set<int> allow = const {},
  }) async =>
      (await _sendRaw(send, retryOnAuth: retryOnAuth, allow: allow)).body;

  /// Runs [send], refreshing the access token once on a 401. Throws
  /// [ApiError] for any status >= 400 that is not listed in [allow].
  Future<http.Response> _sendRaw(
    Future<http.Response> Function() send, {
    Set<int> allow = const {},
    Duration timeout = const Duration(seconds: 20),
    bool retryOnAuth = true,
  }) async {
    var response = await _perform(send, timeout);

    if (retryOnAuth && response.statusCode == 401 && onRefreshNeeded != null) {
      final token = await _refreshOnce();
      if (token == null) throw _errorFrom(response);
      accessToken = token;
      response = await _perform(send, timeout); // headers rebuilt per call
    }

    if (response.statusCode >= 400 && !allow.contains(response.statusCode)) {
      throw _errorFrom(response);
    }
    return response;
  }

  Future<http.Response> _perform(
    Future<http.Response> Function() send,
    Duration timeout,
  ) async {
    try {
      final response = await send().timeout(timeout);
      offline.value = false;
      return response;
    } on ApiError {
      rethrow;
    } catch (_) {
      // Any transport failure is "offline" as far as a user in Lagos traffic
      // is concerned: DNS, TLS, timeout, dropped socket, web CORS preflight.
      offline.value = true;
      throw ApiError.offline();
    }
  }

  /// Concurrent 401s share one refresh: five screens loading at once must not
  /// rotate the refresh token five times, which reuse detection would read as
  /// an attack and answer by killing the whole family.
  Future<String?> _refreshOnce() {
    return _refreshing ??= onRefreshNeeded!().whenComplete(() {
      _refreshing = null;
    });
  }

  /// Builds the [ApiError] for a failed response — and, when the server
  /// rejected the *tenant*, tells the session so the user is sent back to the
  /// school picker. Every throw site in this class comes through here, so this
  /// is the one place that has to notice.
  ApiError _errorFrom(http.Response response) {
    final header = response.headers['retry-after'];
    final seconds = header == null ? null : int.tryParse(header);
    final error = ApiError.fromBody(
      response.statusCode,
      response.body,
      retryAfter: seconds == null ? null : Duration(seconds: seconds),
    );
    if (tenantSlug != null && _tenantIsDead.contains(error.code)) {
      _rejectTenant();
    }
    return error;
  }

  /// Deliberately narrower than [ApiError.requiresSchool], which also covers
  /// `TENANT_NOT_FOUND`. That one is the ordinary answer to a mistyped school
  /// code on the picker, and ending someone's session over a typo is worse
  /// than the problem this solves. The `tenantSlug != null` guard says the same
  /// thing a second way: the lookup runs with no tenant selected.
  static const _tenantIsDead = {'TENANT_SUSPENDED', 'TENANT_MISMATCH'};

  /// Five screens loading at once produce five rejections and one handler run.
  /// The error itself still propagates to each caller — only the sign-out is
  /// shared, so the screens keep showing the server's own message.
  void _rejectTenant() {
    final handler = onTenantRejected;
    if (handler == null) return;
    _rejecting ??= handler().whenComplete(() {
      _rejecting = null;
    });
  }

  static final _random = Random.secure();

  /// Also the `Idempotency-Key`, so it has to be wide enough that two devices
  /// in the same school never collide: 128 bits.
  static String _newId() => List.generate(
    16,
    (_) => _random.nextInt(256).toRadixString(16).padLeft(2, '0'),
  ).join();

  void dispose() {
    _http.close();
    offline.dispose();
  }
}

class OutboxResult {
  const OutboxResult({
    required this.sent,
    required this.rejected,
    this.expired = 0,
  });

  final int sent;
  final int rejected;

  /// Dropped for being too old to mean what it meant when it was written.
  final int expired;
}

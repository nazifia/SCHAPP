import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../api/api_error.dart';
import '../api/cache.dart';
import '../api/client.dart';
import '../phone.dart';

/// What an unauthenticated client may know about a school: enough to brand the
/// sign-in screen and to name things in that institution's own words.
class Tenant {
  const Tenant({
    required this.slug,
    required this.name,
    required this.institutionType,
    required this.status,
    this.branding = const {},
    this.labels = const {},
  });

  final String slug;
  final String name;
  final String institutionType;
  final String status;
  final Map<String, dynamic> branding;
  final Map<String, dynamic> labels;

  String? get primaryColor => branding['primary_color'] as String?;
  String? get logo => branding['logo'] as String?;
  String? get motto => branding['motto'] as String?;

  /// TRIAL, ACTIVE and PAST_DUE are servable; the backend refuses the rest.
  bool get servable => const {'TRIAL', 'ACTIVE', 'PAST_DUE'}.contains(status);

  factory Tenant.fromJson(Map<String, dynamic> json) => Tenant(
    slug: json['slug'] as String,
    name: json['name'] as String,
    institutionType: json['institution_type'] as String? ?? 'SECONDARY',
    status: json['status'] as String? ?? 'ACTIVE',
    branding: (json['branding'] as Map?)?.cast<String, dynamic>() ?? const {},
    labels: (json['labels'] as Map?)?.cast<String, dynamic>() ?? const {},
  );

  Map<String, dynamic> toJson() => {
    'slug': slug,
    'name': name,
    'institution_type': institutionType,
    'status': status,
    'branding': branding,
    'labels': labels,
  };
}

class OtpChallenge {
  const OtpChallenge({
    required this.requestId,
    required this.phone,
    required this.maskedPhone,
    required this.expiresIn,
    required this.resendAfter,
  });

  final String requestId;
  final String phone;
  final String maskedPhone;
  final Duration expiresIn;
  final Duration resendAfter;
}

/// The one piece of app-wide state: which school, which user, which tokens.
///
/// A [ChangeNotifier] rather than a state-management package — go_router
/// listens to it for redirects and two screens read it. Nothing here needs
/// selectors, families or code generation.
class Session extends ChangeNotifier {
  Session({
    required this.api,
    required this.store,
    FlutterSecureStorage? secureStorage,
  }) : _secure = secureStorage ?? const FlutterSecureStorage() {
    api.onRefreshNeeded = refresh;
    api.onTenantRejected = _tenantRejected;
  }

  final ApiClient api;
  final OfflineStore store;
  final FlutterSecureStorage _secure;

  static const _kTenant = 'tenant';
  static const _kAccess = 'access';
  static const _kRefresh = 'refresh';
  static const _kUser = 'user';
  static const _kDeviceId = 'device_id';

  Tenant? tenant;
  Map<String, dynamic>? user;
  String? _refreshToken;
  String _deviceId = '';
  bool _inRefresh = false;

  bool get signedIn => user != null && _refreshToken != null;
  String get fullName => user?['full_name'] as String? ?? '';
  List<String> get roles =>
      (user?['roles'] as List?)?.cast<String>() ?? const [];
  bool get hasPin => user?['has_pin'] as bool? ?? false;

  /// Server-declared permission codes. Hiding a button is courtesy — the API
  /// enforces this again on every call.
  ///
  /// `*` is the wildcard the school_admin role carries, and it arrives *on its
  /// own* rather than expanded into every code — so without this the person
  /// who can do everything was shown a parent's app.
  bool can(String permission) {
    final codes = (user?['permissions'] as List?)?.cast<String>() ?? const [];
    return codes.contains('*') || codes.contains(permission);
  }

  /// Institution's own word for a thing: Term or Semester, Class or Level.
  /// Falls back to the key so a new label added on the server cannot crash an
  /// older build.
  String label(String key) => tenant?.labels[key] as String? ?? key;

  // --- lifecycle -----------------------------------------------------------

  Future<void> restore() async {
    final all = await _secure.readAll();
    _deviceId = all[_kDeviceId] ?? await _mintDeviceId();
    final tenantJson = all[_kTenant];
    if (tenantJson != null) {
      tenant = Tenant.fromJson(jsonDecode(tenantJson) as Map<String, dynamic>);
      api.tenantSlug = tenant!.slug;
    }
    api.accessToken = all[_kAccess];
    _refreshToken = all[_kRefresh];
    final userJson = all[_kUser];
    if (userJson != null) {
      user = jsonDecode(userJson) as Map<String, dynamic>;
    }
    notifyListeners();
  }

  Future<String> _mintDeviceId() async {
    final random = Random.secure();
    final id = List.generate(
      16,
      (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'),
    ).join();
    await _secure.write(key: _kDeviceId, value: id);
    return id;
  }

  Map<String, dynamic> get _device => {
    'device_id': _deviceId,
    'platform': kIsWeb
        ? 'WEB'
        : defaultTargetPlatform == TargetPlatform.iOS
        ? 'IOS'
        : 'ANDROID',
    'app_version': '1.0.0',
  };

  // --- school --------------------------------------------------------------

  /// Look a school up by its short code. Throws [ApiError] with
  /// `TENANT_NOT_FOUND` when the code is wrong.
  Future<Tenant> chooseSchool(String code) async {
    api.tenantSlug =
        null; // the lookup is a public route, and the old slug may be dead
    final response = await api.get(
      '/public/tenants/lookup/',
      query: {'slug': code.trim().toLowerCase()},
    );
    final found = Tenant.fromJson(response.data as Map<String, dynamic>);
    if (!found.servable) {
      throw const ApiError(
        code: 'TENANT_SUSPENDED',
        message: "This institution's account is not active.",
        status: 403,
      );
    }
    // A different school means the previous one's cached rows must go.
    if (tenant != null && tenant!.slug != found.slug) await _wipe();
    tenant = found;
    api.tenantSlug = found.slug;
    await _secure.write(key: _kTenant, value: jsonEncode(found.toJson()));
    notifyListeners();
    return found;
  }

  /// Wired into [ApiClient.onTenantRejected]: the school this session belongs
  /// to has been suspended, or the token was minted for a different one.
  ///
  /// The session goes too, not just the school. Keeping the tokens would send
  /// the user from the picker straight back to a signed-in screen holding a
  /// token for the school they just left — which the server answers with
  /// `TENANT_MISMATCH`, which lands here again, forever. `notifyServer: false`
  /// because this runs inside a failed request: the logout call would be
  /// rejected by the same tenant check.
  Future<void> _tenantRejected() async {
    await signOut(notifyServer: false);
    await forgetSchool();
  }

  Future<void> forgetSchool() async {
    await _wipe();
    tenant = null;
    api.tenantSlug = null;
    await _secure.delete(key: _kTenant);
    notifyListeners();
  }

  // --- sign in -------------------------------------------------------------

  /// Requests a code. The response is identical for numbers with no account,
  /// so never tell the user "no account found" on this path.
  Future<OtpChallenge> requestOtp(String rawPhone) async {
    final phone = normalizePhone(rawPhone); // throws InvalidMsisdn on shape
    final response = await api.post(
      '/auth/otp/request/',
      body: {'phone': phone},
    );
    final data = response.data as Map<String, dynamic>;
    return OtpChallenge(
      requestId: data['request_id'] as String,
      phone: phone,
      maskedPhone: data['masked_phone'] as String? ?? maskPhone(phone),
      expiresIn: Duration(seconds: data['expires_in'] as int? ?? 300),
      resendAfter: Duration(seconds: data['resend_after'] as int? ?? 60),
    );
  }

  Future<void> verifyOtp(String requestId, String code) async {
    final response = await api.post(
      '/auth/otp/verify/',
      body: {'request_id': requestId, 'code': code, 'device': _device},
    );
    await _adopt(response.data as Map<String, dynamic>);
  }

  /// Second factor for staff on an already-registered device. Fails as
  /// `PIN_INVALID` for every reason, including an unknown device — fall back
  /// to OTP rather than explaining which.
  Future<void> pinLogin(String rawPhone, String pin) async {
    final response = await api.post(
      '/auth/pin/login/',
      body: {'phone': normalizePhone(rawPhone), 'pin': pin, 'device': _device},
    );
    await _adopt(response.data as Map<String, dynamic>);
  }

  Future<void> setPin(String pin) async {
    await api.post('/auth/pin/set/', body: {'pin': pin, 'confirm_pin': pin});
    user = {...?user, 'has_pin': true};
    await _secure.write(key: _kUser, value: jsonEncode(user));
    notifyListeners();
  }

  Future<void> _adopt(Map<String, dynamic> payload) async {
    api.accessToken = payload['access'] as String?;
    _refreshToken = payload['refresh'] as String?;
    user = (payload['user'] as Map?)?.cast<String, dynamic>();
    await _secure.write(key: _kAccess, value: api.accessToken);
    await _secure.write(key: _kRefresh, value: _refreshToken);
    await _secure.write(key: _kUser, value: jsonEncode(user));
    notifyListeners();
    // Anything written while signed out — or before the token expired — goes now.
    await api.flushOutbox();
  }

  // --- tokens --------------------------------------------------------------

  /// Wired into [ApiClient.onRefreshNeeded]. Returns null when the session is
  /// unrecoverable, which signs the user out.
  ///
  /// The guard exists because the refresh call goes through the same client: a
  /// 401 on the refresh endpoint itself must not ask for another refresh.
  Future<String?> refresh() async {
    if (_inRefresh || _refreshToken == null) return null;
    _inRefresh = true;
    try {
      final response = await api.post(
        '/auth/token/refresh/',
        body: {'refresh': _refreshToken},
        // A 401 here is the answer, not a reason to refresh again — the retry
        // would await the very future this call is inside of.
        retryOnAuth: false,
        // No bearer token: the access token is expired by definition here, and
        // DRF authenticates before it reaches this AllowAny view, so sending it
        // gets a 401 TOKEN_NOT_VALID instead of a new pair.
        auth: false,
      );
      final data = response.data as Map<String, dynamic>;
      api.accessToken = data['access'] as String?;
      _refreshToken = data['refresh'] as String?;
      await _secure.write(key: _kAccess, value: api.accessToken);
      await _secure.write(key: _kRefresh, value: _refreshToken);
      return api.accessToken;
    } on ApiError catch (error) {
      // Unreachable server: the session is fine, the network is not. Returning
      // null here would make the caller throw the original 401, which
      // `ApiClient.get` cannot recognise as offline — so the cached copy is
      // skipped and an offline user is told their credentials are missing.
      if (error.isOffline) rethrow;
      // TOKEN_REUSE_DETECTED means the family was revoked: someone replayed a
      // rotated token and the server cannot tell which holder is the attacker.
      if (error.requiresSignIn) await signOut(notifyServer: false);
      return null;
    } finally {
      _inRefresh = false;
    }
  }

  Future<void> signOut({bool notifyServer = true}) async {
    if (notifyServer && _refreshToken != null) {
      try {
        await api.post('/auth/logout/');
      } on ApiError {
        // Offline or already-dead token: local state still has to be cleared.
      }
    }
    user = null;
    _refreshToken = null;
    api.accessToken = null;
    await _secure.delete(key: _kAccess);
    await _secure.delete(key: _kRefresh);
    await _secure.delete(key: _kUser);
    await _wipe();
    notifyListeners();
  }

  /// Cached rows outlive the token unless they are deleted, and the next
  /// person to hold the phone must not read them.
  Future<void> _wipe() => store.clear();

  // --- account -------------------------------------------------------------

  Future<List<Map<String, dynamic>>> devices() async {
    final response = await api.get('/auth/devices/');
    return (response.data as List).cast<Map<String, dynamic>>();
  }

  Future<void> revokeDevice(String id) async {
    await api.delete('/auth/devices/$id/');
  }

  Future<void> reloadUser() async {
    final response = await api.get('/auth/me/');
    user = (response.data as Map).cast<String, dynamic>();
    await _secure.write(key: _kUser, value: jsonEncode(user));
    notifyListeners();
  }
}

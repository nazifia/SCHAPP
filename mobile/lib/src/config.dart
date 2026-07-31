import 'package:flutter/foundation.dart';

/// Compile-time configuration.
///
/// Override per build:
///   flutter run --dart-define=SCHAPP_API_BASE=https://api.schapp.ng
///
/// The default targets a local `python manage.py runserver` with no flags:
/// `10.0.2.2` is the Android emulator's alias for the host machine, which
/// only exists there — a browser or desktop build reaches the same server as
/// `localhost`.
class Config {
  const Config._();

  static const _defined = String.fromEnvironment('SCHAPP_API_BASE');

  static final apiBase = _defined.isNotEmpty
      ? _defined
      : kIsWeb || defaultTargetPlatform != TargetPlatform.android
      ? 'http://localhost:8000'
      : 'http://10.0.2.2:8000';

  static final apiRoot = '$apiBase/api/v1';
}

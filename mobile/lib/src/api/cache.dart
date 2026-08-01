import 'dart:convert';
import 'dart:typed_data';

import 'package:shared_preferences/shared_preferences.dart';

/// Offline layer: a read-through cache of GET responses and a write outbox.
///
/// ponytail: two `shared_preferences` string keys holding one JSON blob each,
/// not sqflite. A school shell caches whole list responses and replays a
/// handful of queued writes, and this works identically on Android, iOS and
/// web with no conditional imports.
///
/// Ceilings, in the order they will bite:
///   * every put rewrites the whole blob — move to sqflite once a single
///     tenant's cache is measured in megabytes;
///   * the cache holds [_maxEntries] responses, oldest evicted first;
///   * replay is at-least-once, and [OutboxEntry.id] is what makes that safe —
///     it goes out as `Idempotency-Key` on the first attempt and on every
///     replay, so the server collapses the duplicates. An entry whose id is
///     regenerated loses that guarantee.
class OfflineStore {
  /// The instance is injectable so tests can hand in
  /// `SharedPreferences.setMockInitialValues` without a plugin.
  OfflineStore([this._prefs]);

  static const _cacheKey = 'schapp.cache.v1';
  static const _outboxKey = 'schapp.outbox.v1';
  static const _filesKey = 'schapp.files.v1';
  static const _maxEntries = 200;

  /// PDFs go in base64 into the same preferences blob the JSON cache uses, so
  /// they are kept on a much shorter leash: the last few printed, nothing
  /// bigger than one report card's worth. A batch of four hundred ID cards is
  /// past this on purpose — it belongs to a desk with a printer, not to a
  /// phone's key-value store.
  static const _maxFiles = 5;
  static const maxFileBytes = 2 * 1024 * 1024;

  SharedPreferences? _prefs;
  Map<String, dynamic>? _cache;
  Map<String, dynamic>? _files;
  List<Map<String, dynamic>>? _outbox;

  Future<SharedPreferences> get _p async =>
      _prefs ??= await SharedPreferences.getInstance();

  // --- response cache ------------------------------------------------------

  Future<Map<String, dynamic>> _loadCache() async {
    if (_cache != null) return _cache!;
    final raw = (await _p).getString(_cacheKey);
    _cache = _decodeMap(raw);
    return _cache!;
  }

  /// [at] is for a rewrite rather than a fetch: folding a queued write into a
  /// cached list must not make an hour-old list claim it was read just now.
  Future<void> put(String key, String body, {DateTime? at}) async {
    final cache = await _loadCache();
    cache[key] = {
      'body': body,
      'at': (at ?? DateTime.now()).toIso8601String(),
    };
    if (cache.length > _maxEntries) {
      final oldest = cache.entries.toList()
        ..sort(
          (a, b) =>
              (a.value['at'] as String).compareTo(b.value['at'] as String),
        );
      for (final entry in oldest.take(cache.length - _maxEntries)) {
        cache.remove(entry.key);
      }
    }
    await (await _p).setString(_cacheKey, jsonEncode(cache));
  }

  Future<CachedBody?> get(String key) async {
    final entry = (await _loadCache())[key];
    if (entry is! Map) return null;
    return CachedBody(
      entry['body'] as String,
      DateTime.tryParse(entry['at'] as String? ?? '') ?? DateTime(1970),
    );
  }

  /// Every cached GET key. The client walks these to fold a queued write into
  /// the lists that already hold the row.
  Future<List<String>> keys() async => (await _loadCache()).keys.toList();

  // --- file cache ----------------------------------------------------------

  Future<Map<String, dynamic>> _loadFiles() async {
    if (_files != null) return _files!;
    _files = _decodeMap((await _p).getString(_filesKey));
    return _files!;
  }

  Future<void> putBytes(String key, Uint8List bytes) async {
    if (bytes.length > maxFileBytes) return;
    final files = await _loadFiles();
    files[key] = {
      'body': base64Encode(bytes),
      'at': DateTime.now().toIso8601String(),
    };
    if (files.length > _maxFiles) {
      final oldest = files.entries.toList()
        ..sort(
          (a, b) =>
              (a.value['at'] as String).compareTo(b.value['at'] as String),
        );
      for (final entry in oldest.take(files.length - _maxFiles)) {
        files.remove(entry.key);
      }
    }
    await (await _p).setString(_filesKey, jsonEncode(files));
  }

  Future<Uint8List?> getBytes(String key) async {
    final entry = (await _loadFiles())[key];
    if (entry is! Map) return null;
    try {
      return base64Decode(entry['body'] as String);
    } catch (_) {
      return null;
    }
  }

  // --- write outbox --------------------------------------------------------

  Future<List<Map<String, dynamic>>> _loadOutbox() async {
    if (_outbox != null) return _outbox!;
    final raw = (await _p).getString(_outboxKey);
    try {
      final decoded = raw == null ? const [] : jsonDecode(raw) as List;
      _outbox = decoded.cast<Map<String, dynamic>>().toList();
    } catch (_) {
      _outbox = [];
    }
    return _outbox!;
  }

  Future<void> _saveOutbox() async {
    await (await _p).setString(_outboxKey, jsonEncode(await _loadOutbox()));
  }

  Future<void> enqueue(OutboxEntry entry) async {
    (await _loadOutbox()).add(entry.toJson());
    await _saveOutbox();
  }

  /// Oldest first: a queued mark-attendance must not overtake the enrolment
  /// change it depends on.
  Future<List<OutboxEntry>> pending() async =>
      (await _loadOutbox()).map(OutboxEntry.fromJson).toList();

  Future<void> dequeue(String id) async {
    (await _loadOutbox()).removeWhere((e) => e['id'] == id);
    await _saveOutbox();
  }

  /// Sign-out and tenant switch: one school's rows must never be readable
  /// from the next session on the same device.
  Future<void> clear() async {
    _cache = {};
    _files = {};
    _outbox = [];
    final prefs = await _p;
    await prefs.remove(_cacheKey);
    await prefs.remove(_filesKey);
    await prefs.remove(_outboxKey);
  }

  Map<String, dynamic> _decodeMap(String? raw) {
    try {
      return raw == null
          ? {}
          : (jsonDecode(raw) as Map).cast<String, dynamic>();
    } catch (_) {
      return {}; // a corrupt cache is an empty cache, never a crash on launch
    }
  }
}

class CachedBody {
  const CachedBody(this.body, this.at);

  final String body;
  final DateTime at;

  Duration get age => DateTime.now().difference(at);
}

class OutboxEntry {
  OutboxEntry({
    required this.id,
    required this.method,
    required this.path,
    required this.body,
    required this.at,
    this.label = '',
  });

  final String id;
  final String method;
  final String path;
  final Map<String, dynamic> body;
  final DateTime at;

  /// Shown in the pending-changes list: "Attendance for JSS1A, 12 Mar".
  final String label;

  Map<String, dynamic> toJson() => {
    'id': id,
    'method': method,
    'path': path,
    'body': body,
    'at': at.toIso8601String(),
    'label': label,
  };

  factory OutboxEntry.fromJson(Map<String, dynamic> json) => OutboxEntry(
    id: json['id'] as String,
    method: json['method'] as String,
    path: json['path'] as String,
    body: (json['body'] as Map).cast<String, dynamic>(),
    at: DateTime.tryParse(json['at'] as String? ?? '') ?? DateTime(1970),
    label: json['label'] as String? ?? '',
  );
}

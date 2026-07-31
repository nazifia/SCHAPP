import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';

/// Bulk intake: the spreadsheet the school already has, sent once.
///
/// The screen exists for the *row report*, not for a success tick. The server
/// runs the file as one transaction, so there is no partial state to render:
/// either every row landed, or nothing was written and each bad line is named
/// by its index — which is the office's edit list for the file on their desk.
class ImportScreen extends StatefulWidget {
  const ImportScreen({super.key, required this.session});

  final Session session;

  @override
  State<ImportScreen> createState() => _ImportScreenState();
}

class _ImportScreenState extends State<ImportScreen> {
  late final Repository _repo = Repository(widget.session.api);
  late final Future<List<Map<String, dynamic>>> _sessions = _repo
      .sessions()
      .catchError((_) => const <Map<String, dynamic>>[]);

  String? _filename;
  Uint8List? _bytes;
  String? _sessionId;
  bool _enforceCapacity = true;
  bool _busy = false;
  Map<String, dynamic>? _report;
  String? _failure;

  Future<void> _pick() async {
    // ponytail: any file rather than FileType.custom + ['csv'] — Android
    // content providers misreport extensions and the importer already rejects
    // a file with no header row. Tighten if users start sending .xlsx.
    final picked = await FilePicker.platform.pickFiles(withData: true);
    if (picked == null || picked.files.isEmpty) return;
    final file = picked.files.first;
    if (file.bytes == null) {
      setState(() => _failure = 'That file could not be read from the device.');
      return;
    }
    setState(() {
      _filename = file.name;
      _bytes = file.bytes;
      _report = null;
      _failure = null;
    });
  }

  Future<void> _upload() async {
    final bytes = _bytes;
    if (bytes == null) return;
    setState(() {
      _busy = true;
      _report = null;
      _failure = null;
    });
    try {
      final report = await _repo.importStudents(
        file: bytes,
        filename: _filename ?? 'students.csv',
        session: _sessionId,
        enforceCapacity: _enforceCapacity,
      );
      if (mounted) setState(() => _report = report);
    } on ApiError catch (error) {
      if (mounted) setState(() => _failure = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final label = widget.session.label('STUDENT').toLowerCase();
    return Scaffold(
      appBar: AppBar(title: Text('Import ${label}s')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(
            'Upload the CSV you already keep. Columns are matched by header '
            'name — first name and last name are required, everything else is '
            'optional and unknown columns are ignored.',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
          const SizedBox(height: 16),
          Card(
            child: ListTile(
              leading: const Icon(Icons.attach_file),
              title: Text(_filename ?? 'No file chosen'),
              subtitle: _bytes == null
                  ? null
                  : Text('${(_bytes!.length / 1024).ceil()} KB'),
              trailing: TextButton(
                onPressed: _busy ? null : _pick,
                child: Text(_bytes == null ? 'Choose' : 'Change'),
              ),
            ),
          ),
          const SizedBox(height: 8),
          FutureBuilder<List<Map<String, dynamic>>>(
            future: _sessions,
            builder: (context, snapshot) {
              final sessions = snapshot.data ?? const [];
              return DropdownButtonFormField<String?>(
                initialValue: _sessionId,
                isExpanded: true,
                decoration: const InputDecoration(
                  labelText: 'Enrol into session',
                  helperText: 'Leave empty to create records without a class.',
                  border: OutlineInputBorder(),
                ),
                items: [
                  const DropdownMenuItem(value: null, child: Text('None')),
                  for (final session in sessions)
                    DropdownMenuItem(
                      value: session['id'] as String?,
                      child: Text('${session['name']}'),
                    ),
                ],
                onChanged: _busy
                    ? null
                    : (value) => setState(() => _sessionId = value),
              );
            },
          ),
          SwitchListTile(
            value: _enforceCapacity,
            onChanged: _busy
                ? null
                : (value) => setState(() => _enforceCapacity = value),
            title: const Text('Respect class capacity'),
            subtitle: const Text(
              'Turn off when loading past years into full classes.',
            ),
          ),
          const SizedBox(height: 8),
          FilledButton.icon(
            onPressed: _busy || _bytes == null ? null : _upload,
            icon: _busy
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.upload_file),
            label: Text(_busy ? 'Importing…' : 'Import'),
          ),
          if (_failure != null) ...[
            const SizedBox(height: 16),
            Card(
              color: Theme.of(context).colorScheme.errorContainer,
              child: ListTile(
                leading: const Icon(Icons.error_outline),
                title: Text(_failure!),
              ),
            ),
          ],
          if (_report != null) ...[
            const SizedBox(height: 16),
            _Report(report: _report!),
          ],
        ],
      ),
    );
  }
}

class _Report extends StatelessWidget {
  const _Report({required this.report});

  final Map<String, dynamic> report;

  @override
  Widget build(BuildContext context) {
    final errors = (report['errors'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
    final created = report['created'] as int? ?? 0;

    if (errors.isEmpty) {
      return Card(
        child: ListTile(
          leading: const Icon(Icons.check_circle_outline),
          title: Text('$created record${created == 1 ? '' : 's'} created'),
        ),
      );
    }

    return Card(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          ListTile(
            leading: const Icon(Icons.report_outlined),
            title: Text(
              '${errors.length} row${errors.length == 1 ? '' : 's'} rejected',
            ),
            // Said plainly, because the obvious reading of "3 rows failed" is
            // that the other 397 went in. They did not.
            subtitle: const Text(
              'Nothing was saved. Fix these lines and upload the file again.',
            ),
          ),
          for (final error in errors.take(50))
            ListTile(
              dense: true,
              // +2: the header is line 1 and a spreadsheet counts from 1, so
              // this is the line number the office is looking at.
              leading: Text('Line ${(error['index'] as int? ?? 0) + 2}'),
              title: Text('${error['identifier']}'),
              subtitle: Text('${error['message']}'),
            ),
          if (errors.length > 50)
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text('…and more. Fix these first.'),
            ),
        ],
      ),
    );
  }
}

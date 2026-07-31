import 'package:flutter/material.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';

/// The morning register: one class, one date, four states per pupil.
///
/// Everyone starts as present, because in a class of forty that is thirty-eight
/// taps saved and the exceptions are what a teacher actually knows. Submitting
/// works offline — the register goes to the outbox and syncs later, versioned
/// the same way marks are.
///
/// When a sync is refused, the server answers 422 with a row report rather than
/// an error, and that report is rendered against the pupils it names: the
/// office corrected three rows while the phone was in a staffroom, and those
/// three are the only thing the teacher has to look at.
class AttendanceScreen extends StatefulWidget {
  const AttendanceScreen({super.key, required this.session});

  final Session session;

  @override
  State<AttendanceScreen> createState() => _AttendanceScreenState();
}

const kAttendanceStates = ['PRESENT', 'ABSENT', 'LATE', 'EXCUSED'];

/// The bulk payload for a whole class. A plain function so the rules that
/// matter — the default, the version, the note — can be tested without a
/// widget, the same way `studentPayload` is.
List<Map<String, dynamic>> attendanceRows({
  required List<Map<String, dynamic>> students,
  required Map<String, String> marks,
  required Map<String, int> versions,
  required Map<String, String> notes,
  DateTime? recordedAt,
}) {
  // One timestamp for the register, not one per row: the teacher took it in a
  // single sweep, and the server keeps this rather than the sync time.
  final at = (recordedAt ?? DateTime.now()).toUtc().toIso8601String();
  return [
    for (final student in students)
      if (student['id'] is String)
        {
          'student': student['id'],
          'status': marks[student['id']] ?? kAttendanceStates.first,
          // Omitted when there is no stored record: a version the client never
          // saw is a create, not a conflict.
          if (versions[student['id']] != null)
            'version': versions[student['id']],
          // Always sent, even untouched. The server writes `note` from what
          // arrives, so leaving it out wipes the reason someone typed for an
          // absence.
          'note': notes[student['id']] ?? '',
          'recorded_at': at,
        },
  ];
}

class _AttendanceScreenState extends State<AttendanceScreen> {
  late final Repository _repo = Repository(widget.session.api);

  Map<String, dynamic>? _classes;
  String? _armId;
  DateTime _date = DateTime.now();
  List<Map<String, dynamic>> _students = const [];
  final _marks = <String, String>{};
  final _versions = <String, int>{};
  final _notes = <String, String>{};

  /// What the server last told us, to tell an edited register from a loaded
  /// one. Switching class or date throws unsaved taps away, and forty of them
  /// is twenty minutes of somebody's morning.
  Map<String, String> _saved = const {};

  /// Per-pupil refusals from the last submit, keyed by student id.
  Map<String, String> _rowErrors = const {};

  /// Bumped to rebuild the class dropdown after a discard is declined — the
  /// field holds its own value, so nothing else puts the old class back.
  int _armEpoch = 0;

  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadClasses();
  }

  String? get _termId => (_classes?['term'] as Map?)?['id'] as String?;

  String get _dateText => _date.toIso8601String().substring(0, 10);

  bool get _dirty {
    for (final student in _students) {
      final id = student['id'] as String;
      if ((_marks[id] ?? kAttendanceStates.first) !=
          (_saved[id] ?? kAttendanceStates.first)) {
        return true;
      }
    }
    return false;
  }

  Future<void> _loadClasses() async {
    setState(() => _busy = true);
    try {
      final classes = await _repo.myClasses();
      if (mounted) setState(() => _classes = classes);
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Load the register for the chosen class and date.
  ///
  /// `preserveMarks` is the after-a-conflict reload: fresh versions and the
  /// server's status for the rows it refused, while every row the teacher
  /// already touched keeps their value. Reloading wholesale would answer "three
  /// rows clashed" by discarding the other thirty-seven.
  Future<void> _loadRegister({bool preserveMarks = false}) async {
    if (_armId == null) return;
    final kept = Map<String, String>.from(_marks);
    final keptNotes = Map<String, String>.from(_notes);
    setState(() => _busy = true);
    try {
      final students = await _repo.students(classArm: _armId);
      final existing = await _repo.attendance(
        classArm: _armId!,
        date: _dateText,
      );
      if (!mounted) return;

      final marks = <String, String>{};
      _versions.clear();
      _notes.clear();
      for (final row in existing) {
        final id = row['student'] as String?;
        if (id == null) continue;
        marks[id] = row['status'] as String? ?? kAttendanceStates.first;
        _versions[id] = row['version'] as int? ?? 1;
        _notes[id] = row['note'] as String? ?? '';
      }

      _marks
        ..clear()
        ..addAll(marks);
      _saved = Map<String, String>.from(marks);

      if (preserveMarks) {
        for (final entry in kept.entries) {
          // A refused row keeps the server's value: that is the correction the
          // conflict is about, and re-sending the stale tap would only clash
          // again.
          if (_rowErrors.containsKey(entry.key)) continue;
          _marks[entry.key] = entry.value;
          _notes[entry.key] = keptNotes[entry.key] ?? _notes[entry.key] ?? '';
        }
      }

      setState(() {
        _students = students;
        _error = null;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<bool> _confirmDiscard() async {
    if (!_dirty) return true;
    final discard = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Leave the register unsaved?'),
        content: const Text(
          'The marks you have changed have not been saved. Leaving this '
          'register loses them.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Keep marking'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Discard'),
          ),
        ],
      ),
    );
    return discard ?? false;
  }

  Future<void> _selectArm(String? value) async {
    if (value == _armId) return;
    if (!await _confirmDiscard()) {
      // ponytail: rebuild the field rather than manage its value here — the
      // form field owns its selection and this is the one place it has to be
      // put back.
      setState(() => _armEpoch++);
      return;
    }
    setState(() {
      _armId = value;
      _rowErrors = const {};
    });
    await _loadRegister();
  }

  Future<void> _pickDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _date,
      firstDate: DateTime(_date.year - 1),
      lastDate: DateTime.now(),
    );
    if (picked == null || !mounted) return;
    if (picked == _date) return;
    if (!await _confirmDiscard()) return;
    setState(() {
      _date = picked;
      _rowErrors = const {};
    });
    await _loadRegister();
  }

  Future<void> _editNote(String id, String name) async {
    final controller = TextEditingController(text: _notes[id] ?? '');
    final note = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(name),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLength: 200, // the server's own limit on the field
          decoration: const InputDecoration(
            labelText: 'Reason',
            hintText: 'Sick note, funeral, sent home',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (note == null) return;
    setState(() => _notes[id] = note);
  }

  Future<void> _submit() async {
    if (_termId == null || _armId == null) return;
    final rows = attendanceRows(
      students: _students,
      marks: _marks,
      versions: _versions,
      notes: _notes,
    );
    if (rows.isEmpty) return;

    setState(() {
      _busy = true;
      _rowErrors = const {};
    });
    try {
      final response = await _repo.markAttendance(
        term: _termId!,
        classArm: _armId!,
        date: _dateText,
        rows: rows,
      );
      if (!mounted) return;

      if (response.queued) {
        showApiMessage(
          context,
          'Register saved on this device. It will sync when there is a '
          'connection.',
        );
        // Treated as saved locally, so switching class does not warn about
        // marks that are already in the outbox.
        setState(() => _saved = Map<String, String>.from(_marks));
        return;
      }

      final report = (response.data as Map?)?.cast<String, dynamic>() ?? {};
      final errors = (report['errors'] as List? ?? const [])
          .cast<Map<String, dynamic>>();
      if (errors.isEmpty) {
        showApiMessage(context, 'Register saved for $_dateText.');
        await _loadRegister();
        return;
      }

      setState(
        () => _rowErrors = {
          for (final error in errors)
            '${error['identifier']}': '${error['message']}',
        },
      );
      showApiMessage(
        context,
        '${errors.length} ${errors.length == 1 ? 'pupil was' : 'pupils were'} '
        'refused — nothing was saved. Check the rows in red.',
      );
      await _loadRegister(preserveMarks: true);
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _showSummary() {
    final term = _termId;
    if (term == null) return;
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (context) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: 0.7,
        builder: (context, controller) => AsyncView<List<Map<String, dynamic>>>(
          load: () => _repo.attendanceSummary(term: term, classArm: _armId),
          empty: const EmptyState(
            icon: Icons.insights_outlined,
            message: 'No attendance has been recorded this term yet.',
          ),
          builder: (context, rows) => ListView.separated(
            controller: controller,
            itemCount: rows.length + 1,
            separatorBuilder: (_, _) => const Divider(height: 1),
            itemBuilder: (context, index) {
              if (index == 0) {
                return ListTile(
                  title: Text(
                    'Attendance this term',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  subtitle: const Text(
                    'Late still counts as present; absent and excused do not.',
                  ),
                );
              }
              final row = rows[index - 1];
              return ListTile(
                dense: true,
                title: Text('${row['full_name']}'),
                subtitle: Text(
                  '${row['present']} present · ${row['absent']} absent · '
                  '${row['late']} late · ${row['excused']} excused',
                ),
                trailing: Text(
                  '${row['percentage']}%',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              );
            },
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final arms = (_classes?['arms'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
    final absent = _marks.values.where((s) => s == 'ABSENT').length;
    final noTerm = _classes != null && _termId == null;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Attendance'),
        actions: [
          IconButton(
            onPressed: _termId == null ? null : _showSummary,
            icon: const Icon(Icons.insights_outlined),
            tooltip: 'Term summary',
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(12),
            child: Row(
              children: [
                Expanded(
                  child: DropdownButtonFormField<String>(
                    key: ValueKey('arm-$_armId-$_armEpoch'),
                    initialValue: _armId,
                    isExpanded: true,
                    decoration: InputDecoration(
                      labelText: widget.session.label('CLASS'),
                    ),
                    items: [
                      for (final arm in arms)
                        DropdownMenuItem(
                          value: arm['id'] as String,
                          child: Text(arm['name'] as String? ?? ''),
                        ),
                    ],
                    onChanged: _busy ? null : _selectArm,
                  ),
                ),
                const SizedBox(width: 12),
                OutlinedButton.icon(
                  onPressed: _busy ? null : _pickDate,
                  icon: const Icon(Icons.calendar_today, size: 18),
                  label: Text(_dateText),
                ),
              ],
            ),
          ),
          if (noTerm)
            const _Notice(
              'No current term is set. Ask the office to set one before taking '
              'the register.',
            ),
          if (_error != null) _Notice(_error!),
          if (_rowErrors.isNotEmpty)
            _Notice(
              'Nothing was saved. ${_rowErrors.length} '
              '${_rowErrors.length == 1 ? 'row' : 'rows'} changed on the '
              'server since this device last synced — the rows below now show '
              'what the office has.',
            ),
          if (_busy) const LinearProgressIndicator(),
          if (_students.isNotEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Row(
                children: [
                  Text('${_students.length} students'),
                  const Spacer(),
                  Text('$absent marked absent'),
                ],
              ),
            ),
          Expanded(
            child: _students.isEmpty
                ? EmptyState(
                    icon: Icons.how_to_reg_outlined,
                    message:
                        'Choose a ${widget.session.label('CLASS').toLowerCase()} '
                        'to open the register.',
                  )
                : ListView.separated(
                    itemCount: _students.length,
                    separatorBuilder: (_, _) => const Divider(height: 1),
                    itemBuilder: (context, index) {
                      final student = _students[index];
                      final id = student['id'] as String;
                      // The list endpoint serves `full_name` only — no
                      // first/last on StudentListSerializer.
                      final name = student['full_name'] as String? ?? '';
                      final failure = _rowErrors[id];
                      final note = _notes[id] ?? '';
                      return ListTile(
                        // Long press rather than a field per row: a note is the
                        // exception on an exception, and forty rows have no
                        // space for one.
                        onLongPress: () => _editNote(id, name),
                        title: Text(name),
                        subtitle: Text(
                          failure ??
                              (note.isNotEmpty
                                  ? note
                                  : student['admission_number'] as String? ??
                                        ''),
                          style: failure == null
                              ? null
                              : TextStyle(
                                  color: Theme.of(context).colorScheme.error,
                                ),
                        ),
                        trailing: SegmentedButton<String>(
                          showSelectedIcon: false,
                          segments: const [
                            ButtonSegment(value: 'PRESENT', label: Text('P')),
                            ButtonSegment(value: 'ABSENT', label: Text('A')),
                            ButtonSegment(value: 'LATE', label: Text('L')),
                            ButtonSegment(value: 'EXCUSED', label: Text('E')),
                          ],
                          selected: {_marks[id] ?? kAttendanceStates.first},
                          onSelectionChanged: (selection) => setState(() {
                            _marks[id] = selection.first;
                            _rowErrors = {..._rowErrors}..remove(id);
                          }),
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
      bottomNavigationBar: _students.isEmpty
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    onPressed: _busy || _termId == null ? null : _submit,
                    icon: const Icon(Icons.save_outlined),
                    label: const Text('Save register'),
                  ),
                ),
              ),
            ),
    );
  }
}

class _Notice extends StatelessWidget {
  const _Notice(this.message);

  final String message;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
    child: Text(
      message,
      style: TextStyle(color: Theme.of(context).colorScheme.error),
    ),
  );
}

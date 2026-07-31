import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';

/// Mark entry for one class, one subject, one component at a time.
///
/// Built for the actual job: a teacher with a paper script pile, entering
/// forty marks down a column. One column at a time, keyboard stays numeric,
/// and **submit works offline** — the whole batch goes to the outbox and syncs
/// later. Each row carries the `version` the device last saw, so a late sync
/// reports a conflict on the three rows the office corrected instead of
/// silently overwriting them.
///
/// Two things are checked here before anything is sent, because the server
/// rolls the whole batch back for one bad row: a mark that is not a number,
/// and a mark above the component's maximum. Whatever the server still refuses
/// comes back as a row report and is shown against the pupil it names.
class ScoreEntryScreen extends StatefulWidget {
  const ScoreEntryScreen({super.key, required this.session});

  final Session session;

  @override
  State<ScoreEntryScreen> createState() => _ScoreEntryScreenState();
}

/// True while a column holds keyed marks that have not been sent.
///
/// Lifted out of the screen because the thing that loses them is a tab tap,
/// and the tab bar is above this screen: switching column asked before
/// discarding and `go('/attendance')` did not, so the safest way out of forty
/// unsent marks was the one route with no question on it. The router's
/// `onExit` reads this.
final unsavedMarks = ValueNotifier<bool>(false);

/// The one question, asked from both places. True means "go ahead".
Future<bool> confirmDiscardMarks(BuildContext context) async {
  if (!unsavedMarks.value) return true;
  final discard = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('Leave these marks unsaved?'),
      content: const Text(
        'The marks you have keyed have not been saved. Leaving this column '
        'loses them.',
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

/// The batch for one component, plus whatever could not be sent.
///
/// Separated from the widget so the rules a mark sheet turns on — a blank is
/// not a zero, a version is only sent for a mark that exists, a maximum is a
/// maximum — are testable without pumping a screen.
class ScoreBatch {
  const ScoreBatch({required this.rows, required this.problems});

  final List<Map<String, dynamic>> rows;

  /// Student id to the reason their entry cannot go, empty when all is well.
  final Map<String, String> problems;

  bool get ok => problems.isEmpty;

  /// Rows that remove a stored mark rather than write one — a null score.
  int get removals => rows.where((row) => row['score'] == null).length;
}

ScoreBatch scoreBatch({
  required List<Map<String, dynamic>> sheetRows,
  required String componentId,
  required Object? maxScore,
  required Map<String, String> typed,
  DateTime? recordedAt,
}) {
  final at = (recordedAt ?? DateTime.now()).toUtc().toIso8601String();
  final max = double.tryParse('$maxScore');
  final rows = <Map<String, dynamic>>[];
  final problems = <String, String>{};

  for (final row in sheetRows) {
    final studentId = row['student'] as String?;
    if (studentId == null) continue;
    final text = (typed[studentId] ?? '').trim();
    final existing = (row['scores'] as Map?)?[componentId] as Map?;

    // A blank is "not marked yet", not a zero. Clearing a mark that *is*
    // stored is an unmark: a null score, carrying the version, so the server
    // removes the row rather than being told nothing at all.
    if (text.isEmpty) {
      if (existing != null) {
        rows.add({
          'student': studentId,
          'score': null,
          'version': existing['version'],
          'recorded_at': at,
        });
      }
      continue;
    }

    final score = double.tryParse(text);
    if (score == null) {
      // Silently dropped before this: the teacher was told "40 marks saved"
      // while the row they fat-fingered was never in the batch at all.
      problems[studentId] = 'That is not a number.';
      continue;
    }
    if (score < 0 || (max != null && score > max)) {
      problems[studentId] = 'Marked out of ${_trim(maxScore)}.';
      continue;
    }

    rows.add({
      'student': studentId,
      'score': text,
      if (existing != null) 'version': existing['version'],
      'recorded_at': at,
    });
  }
  // Nothing to send while anything is wrong: the server would roll the batch
  // back anyway, and a half-batch that skips the bad rows is exactly the
  // silent partial save this screen is meant to stop.
  return ScoreBatch(
    rows: problems.isEmpty ? rows : const [],
    problems: problems,
  );
}

class _ScoreEntryScreenState extends State<ScoreEntryScreen> {
  late final Repository _repo = Repository(widget.session.api);

  Map<String, dynamic>? _classes;
  String? _subjectId;
  String? _armId;
  String? _componentId;
  Map<String, dynamic>? _sheet;
  final _entries = <String, TextEditingController>{};

  /// What the sheet held when it was loaded, per student, for the column on
  /// screen. Anything else is unsaved work worth warning about.
  Map<String, String> _saved = const {};

  /// Per-pupil refusals — the server's row report, or what this screen
  /// refused to send in the first place.
  Map<String, String> _rowErrors = const {};

  /// Bumped to put a dropdown back after a discard is declined; the form
  /// fields own their selection and nothing else restores it.
  int _epoch = 0;

  int _entered = 0;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadClasses();
  }

  @override
  void dispose() {
    for (final controller in _entries.values) {
      controller.dispose();
    }
    // The screen is gone; whatever it held is not the next screen's problem.
    unsavedMarks.value = false;
    super.dispose();
  }

  Future<void> _loadClasses() async {
    setState(() => _busy = true);
    try {
      final classes = await _repo.myClasses();
      if (!mounted) return;
      setState(() {
        _classes = classes;
        _error = null;
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String? get _termId => (_classes?['term'] as Map?)?['id'] as String?;

  /// Load the sheet for the chosen subject and class.
  ///
  /// `preserveEntries` is the after-a-conflict reload: fresh versions for
  /// every row and the server's mark for the rows it refused, while the marks
  /// the teacher keyed keep their place. Rebuilding wholesale would answer
  /// "three rows clashed" by emptying the other thirty-seven fields.
  Future<void> _loadSheet({bool preserveEntries = false}) async {
    if (_termId == null || _subjectId == null) return;
    setState(() => _busy = true);
    try {
      final sheet = await _repo.markSheet(
        term: _termId!,
        subject: _subjectId!,
        classArm: _armId,
      );
      if (!mounted) return;
      final components = (sheet['components'] as List? ?? const [])
          .cast<Map<String, dynamic>>();
      setState(() {
        _sheet = sheet;
        // Keep the column being marked across a reload; it only moves when the
        // subject no longer has it.
        if (!components.any((c) => c['id'] == _componentId)) {
          _componentId = components.isEmpty
              ? null
              : components.first['id'] as String;
        }
        _error = null;
      });
      _rebuildEntries(preserve: preserveEntries);
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Refill the fields with the marks already stored for the chosen column.
  void _rebuildEntries({bool preserve = false}) {
    final kept = {
      for (final entry in _entries.entries) entry.key: entry.value.text,
    };
    for (final controller in _entries.values) {
      controller.dispose();
    }
    _entries.clear();

    final saved = <String, String>{};
    for (final row in _rows) {
      final id = row['student'] as String;
      final existing = (row['scores'] as Map?)?[_componentId] as Map?;
      final stored = existing == null ? '' : _trim(existing['score']);
      saved[id] = stored;
      // A refused row shows the server's mark: that correction is what the
      // conflict is about, and re-sending the stale keystroke only clashes
      // again.
      final text = preserve && !_rowErrors.containsKey(id)
          ? (kept[id] ?? stored)
          : stored;
      _entries[id] = TextEditingController(text: text);
    }

    setState(() {
      _saved = saved;
      _entered = _countEntered();
    });
    _publishDirty();
  }

  int _countEntered() =>
      _entries.values.where((c) => c.text.trim().isNotEmpty).length;

  bool get _dirty {
    for (final entry in _entries.entries) {
      if (entry.value.text.trim() != (_saved[entry.key] ?? '')) return true;
    }
    return false;
  }

  void _publishDirty() => unsavedMarks.value = _dirty;

  List<Map<String, dynamic>> get _rows =>
      (_sheet?['rows'] as List? ?? const []).cast<Map<String, dynamic>>();

  List<Map<String, dynamic>> get _components =>
      (_sheet?['components'] as List? ?? const []).cast<Map<String, dynamic>>();

  Map<String, dynamic>? get _component => _components
      .where((component) => component['id'] == _componentId)
      .firstOrNull;

  Future<bool> _confirmDiscard() {
    _publishDirty();
    return confirmDiscardMarks(context);
  }

  Future<void> _selectSubject(String? value) async {
    if (value == _subjectId) return;
    if (!await _confirmDiscard()) {
      setState(() => _epoch++);
      return;
    }
    setState(() {
      _subjectId = value;
      _rowErrors = const {};
    });
    await _loadSheet();
  }

  Future<void> _selectArm(String? value) async {
    if (value == _armId) return;
    if (!await _confirmDiscard()) {
      setState(() => _epoch++);
      return;
    }
    setState(() {
      _armId = value;
      _rowErrors = const {};
    });
    await _loadSheet();
  }

  Future<void> _selectComponent(String id) async {
    if (id == _componentId) return;
    // The other columns are in the same sheet, so switching used to look free
    // — and quietly threw away a column of keying.
    if (!await _confirmDiscard()) return;
    setState(() {
      _componentId = id;
      _rowErrors = const {};
    });
    _rebuildEntries();
  }

  /// A keystroke clears that row's complaint and keeps the counter honest.
  /// Only rebuilds when something visible actually moved.
  void _touched(String id) {
    _publishDirty();
    final count = _countEntered();
    if (count == _entered && !_rowErrors.containsKey(id)) return;
    setState(() {
      _entered = count;
      _rowErrors = {..._rowErrors}..remove(id);
    });
  }

  Future<void> _submit() async {
    final component = _component;
    if (component == null) return;

    final batch = scoreBatch(
      sheetRows: _rows,
      componentId: _componentId!,
      maxScore: component['max_score'],
      typed: {
        for (final entry in _entries.entries) entry.key: entry.value.text,
      },
    );

    if (!batch.ok) {
      setState(() => _rowErrors = batch.problems);
      showApiMessage(
        context,
        '${batch.problems.length} '
        '${batch.problems.length == 1 ? 'mark needs' : 'marks need'} fixing '
        'before this can be sent.',
      );
      return;
    }
    if (batch.rows.isEmpty) {
      showApiMessage(context, 'Nothing to send — no marks entered.');
      return;
    }

    setState(() {
      _busy = true;
      _rowErrors = const {};
    });
    try {
      final response = await _repo.submitScores(
        term: _termId!,
        subject: _subjectId!,
        component: _componentId!,
        classArm: _armId,
        rows: batch.rows,
      );
      if (!mounted) return;

      if (response.queued) {
        showApiMessage(
          context,
          '${_summary(batch)} on this device. They will sync when there is a '
          'connection.',
        );
        // Already in the outbox, so switching column must not warn about them.
        setState(
          () => _saved = {
            for (final entry in _entries.entries)
              entry.key: entry.value.text.trim(),
          },
        );
        _publishDirty();
        return;
      }

      final report = (response.data as Map?)?.cast<String, dynamic>() ?? {};
      final errors = (report['errors'] as List? ?? const [])
          .cast<Map<String, dynamic>>();
      if (errors.isEmpty) {
        showApiMessage(context, '${_summary(batch)}.');
        await _loadSheet();
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
        '${errors.length} ${errors.length == 1 ? 'row was' : 'rows were'} '
        'refused — nothing was saved. Check the rows in red.',
      );
      await _loadSheet(preserveEntries: true);
    } on ApiError catch (e) {
      if (!mounted) return;
      // A whole-batch refusal: the term is closed, the results are published,
      // or this is not the teacher's subject. The message says which.
      showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final subjects = (_classes?['subjects'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
    final arms = (_classes?['arms'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
    final component = _component;

    return Scaffold(
      appBar: AppBar(
        title: Text(
          'Enter ${widget.session.label('SUBJECT').toLowerCase()} marks',
        ),
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              children: [
                if (_classes != null && _termId == null)
                  const _Notice(
                    'No current term is set. Ask the office to set one before '
                    'entering marks.',
                  ),
                DropdownButtonFormField<String>(
                  key: ValueKey('subject-$_subjectId-$_epoch'),
                  initialValue: _subjectId,
                  isExpanded: true,
                  decoration: InputDecoration(
                    labelText: widget.session.label('SUBJECT'),
                  ),
                  items: [
                    for (final subject in subjects)
                      DropdownMenuItem(
                        value: subject['id'] as String,
                        child: Text('${subject['code']} — ${subject['title']}'),
                      ),
                  ],
                  onChanged: _busy ? null : _selectSubject,
                ),
                const SizedBox(height: 12),
                DropdownButtonFormField<String>(
                  key: ValueKey('arm-$_armId-$_epoch'),
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
              ],
            ),
          ),
          if (_components.isNotEmpty)
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Row(
                children: [
                  for (final item in _components)
                    Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: ChoiceChip(
                        label: Text(
                          '${item['code']} /${_trim(item['max_score'])}',
                        ),
                        selected: item['id'] == _componentId,
                        onSelected: _busy
                            ? null
                            : (_) => _selectComponent(item['id'] as String),
                      ),
                    ),
                ],
              ),
            ),
          if (_error != null) _Notice(_error!),
          if (_rowErrors.isNotEmpty)
            _Notice(
              'Nothing was saved. ${_rowErrors.length} '
              '${_rowErrors.length == 1 ? 'row is' : 'rows are'} shown in red '
              'below — fix those and send again.',
            ),
          if (_busy) const LinearProgressIndicator(),
          if (_rows.isNotEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Row(
                children: [
                  Text('${_rows.length} students'),
                  const Spacer(),
                  // The answer to "have I done this column yet", which a
                  // teacher otherwise gets by scrolling forty rows.
                  Text('$_entered of ${_rows.length} entered'),
                ],
              ),
            ),
          Expanded(
            child: _rows.isEmpty
                ? const EmptyState(
                    icon: Icons.edit_note_outlined,
                    message:
                        'Choose a subject and class to load the mark sheet.',
                  )
                : ListView.separated(
                    itemCount: _rows.length,
                    separatorBuilder: (_, _) => const Divider(height: 1),
                    itemBuilder: (context, index) {
                      final row = _rows[index];
                      final id = row['student'] as String;
                      final failure = _rowErrors[id];
                      return ListTile(
                        title: Row(
                          children: [
                            Flexible(
                              child: Text(row['full_name'] as String? ?? ''),
                            ),
                            // A repeat of last year's subject sitting in this
                            // class: the row most easily marked as the wrong
                            // person's.
                            if (row['is_carryover'] == true) ...[
                              const SizedBox(width: 8),
                              const _Tag('carryover'),
                            ],
                          ],
                        ),
                        subtitle: Text(
                          failure ?? (row['admission_number'] as String? ?? ''),
                          style: failure == null
                              ? null
                              : TextStyle(
                                  color: Theme.of(context).colorScheme.error,
                                ),
                        ),
                        trailing: SizedBox(
                          width: 90,
                          child: TextField(
                            controller: _entries[id],
                            textAlign: TextAlign.right,
                            onChanged: (_) => _touched(id),
                            keyboardType: const TextInputType.numberWithOptions(
                              decimal: true,
                            ),
                            inputFormatters: [
                              FilteringTextInputFormatter.allow(
                                RegExp(r'[0-9.]'),
                              ),
                            ],
                            decoration: InputDecoration(
                              isDense: true,
                              hintText: component == null
                                  ? ''
                                  : '/${_trim(component['max_score'])}',
                            ),
                          ),
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
      bottomNavigationBar: _rows.isEmpty
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    onPressed: _busy || component == null ? null : _submit,
                    icon: const Icon(Icons.save_outlined),
                    label: Text('Save ${component?['code'] ?? ''} marks'),
                  ),
                ),
              ),
            ),
    );
  }
}

/// "37 marks saved", "37 marks saved and 3 removed" — a cleared field is a
/// removal, and telling a teacher it was "saved" is how a mark comes back.
String _summary(ScoreBatch batch) {
  final removed = batch.removals;
  final saved = batch.rows.length - removed;
  if (removed == 0) return '$saved marks saved';
  if (saved == 0) return '$removed ${removed == 1 ? 'mark' : 'marks'} removed';
  return '$saved marks saved and $removed removed';
}

String _trim(Object? value) {
  final number = double.tryParse('$value');
  if (number == null) return '$value';
  return number == number.roundToDouble() ? '${number.toInt()}' : '$number';
}

class _Tag extends StatelessWidget {
  const _Tag(this.label);

  final String label;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: scheme.secondaryContainer,
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        label,
        style: Theme.of(
          context,
        ).textTheme.labelSmall?.copyWith(color: scheme.onSecondaryContainer),
      ),
    );
  }
}

class _Notice extends StatelessWidget {
  const _Notice(this.message);

  final String message;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.all(12),
    child: Text(
      message,
      style: TextStyle(color: Theme.of(context).colorScheme.error),
    ),
  );
}

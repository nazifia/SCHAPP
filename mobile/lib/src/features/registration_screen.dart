import 'package:flutter/material.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';

/// The other half of the approval queue: the screen that puts rows in it.
///
/// Registration existed only as an endpoint, so the queue on `/approvals` was
/// empty at every school that had not written its own client — an adviser had
/// a screen to sign things on and nothing ever arrived there. This is a pupil,
/// a term, and the courses they are taking, sent in one call.
///
/// Registry-side rather than student-side, because that is who holds
/// `academics.manage_enrolment` and who the enrolment endpoint answers to. A
/// student self-service portal is a bigger thing than this: it needs its own
/// permission and its own "my enrolment" endpoint.
class RegistrationScreen extends StatefulWidget {
  const RegistrationScreen({super.key, required this.session});

  final Session session;

  @override
  State<RegistrationScreen> createState() => _RegistrationScreenState();
}

/// What a course already on the student's term says about itself.
const _statusLabel = {
  'DRAFT': 'Draft',
  'SUBMITTED': 'Waiting on adviser',
  'ADVISER_APPROVED': 'Waiting on HOD',
  'APPROVED': 'Registered',
  'REJECTED': 'Refused',
  'DROPPED': 'Dropped',
};

/// Can this course be (re-)registered?
///
/// A refused or dropped course keeps its row — `unique_subject_per_term` means
/// there can only ever be one — and the server revives it rather than writing
/// a second. That is the retry both of those states tell the student to make,
/// so the tick box stays live for them and only for them.
bool canPick(String? status) =>
    status == null || status == 'REJECTED' || status == 'DROPPED';

/// Credit units of a set of courses. The ceiling is the programme's and the
/// server owns it; this is only so the person ticking can see it coming.
int totalUnits(Iterable<Map<String, dynamic>> subjects) => subjects.fold(
  0,
  (sum, subject) => sum + (int.tryParse('${subject['credit_units'] ?? 0}') ?? 0),
);

/// Everything one student's term needs, fetched together.
class _Sheet {
  _Sheet(this.enrolment, this.subjects, this.existing);

  /// Null means this student has no enrolment in the term's session, which is
  /// not an error — it is the answer, and nothing can be registered until the
  /// registry fixes it.
  final Map<String, dynamic>? enrolment;
  final List<Map<String, dynamic>> subjects;

  /// Subject id to the registration already on this term, if any.
  final Map<String, Map<String, dynamic>> existing;
}

class _RegistrationScreenState extends State<RegistrationScreen> {
  final _key = GlobalKey<AsyncViewState<_Sheet>>();
  late final Repository _repo = Repository(widget.session.api);

  List<Map<String, dynamic>> _terms = const [];
  Map<String, dynamic>? _term;
  Map<String, dynamic>? _student;
  String _courseSearch = '';
  bool _busy = false;

  /// Subject id to the course itself, ticked but not yet sent. Cleared
  /// whenever the student or the term changes — a tick means "this pupil, this
  /// term" and nothing else.
  ///
  /// The course and not just its id, because a search replaces the list on
  /// screen while the ticks stay: counted off what is visible, the units on the
  /// bar dropped every course the current search had scrolled away.
  final _picked = <String, Map<String, dynamic>>{};

  @override
  void initState() {
    super.initState();
    _loadTerms();
  }

  Future<void> _loadTerms() async {
    try {
      final terms = await _repo.terms();
      if (!mounted) return;
      setState(() {
        _terms = terms;
        _term = terms.firstWhere(
          (term) => term['is_current'] == true,
          orElse: () => terms.isEmpty ? <String, dynamic>{} : terms.first,
        );
        if (_term!.isEmpty) _term = null;
      });
      _key.currentState?.reload();
    } on ApiError {
      // Leave the picker empty; nothing can be registered without a term and
      // the screen says so rather than half-working.
    }
  }

  Future<_Sheet> _load() async {
    final student = _student;
    final term = _term;
    if (student == null || term == null) {
      return _Sheet(null, const [], const {});
    }
    // The enrolment is per session, not per term: one row covers all three.
    final enrolments = await _repo.enrolments(
      student: student['id'] as String,
      session: term['session'] as String?,
    );
    final enrolment = enrolments.isEmpty ? null : enrolments.first;
    // Without an enrolment there is nothing to narrow the catalogue against,
    // and nothing to register either — the empty state below is what shows.
    final subjects = enrolment == null
        ? const <Map<String, dynamic>>[]
        : await _repo.subjects(
            active: true,
            forEnrolment: enrolment['id'] as String,
            term: term['id'] as String,
            search: _courseSearch.isEmpty ? null : _courseSearch,
          );
    final existing = enrolment == null
        ? const <Map<String, dynamic>>[]
        : await _repo.registrations(
            term: term['id'] as String,
            enrolment: enrolment['id'] as String,
          );
    return _Sheet(enrolment, subjects, {
      for (final row in existing) row['subject'] as String: row,
    });
  }

  void _reload() {
    setState(_picked.clear);
    _key.currentState?.reload();
  }

  Future<void> _pickStudent() async {
    final student = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (context) => _StudentPicker(repo: _repo),
    );
    if (student == null) return;
    setState(() => _student = student);
    _reload();
  }

  Future<void> _submit(_Sheet sheet) async {
    setState(() => _busy = true);
    try {
      final written = await _repo.registerSubjects(
        enrolment: sheet.enrolment!['id'] as String,
        term: _term!['id'] as String,
        subjects: _picked.keys.toList(),
      );
      if (!mounted) return;
      // A secondary school has no adviser workflow and the server approves on
      // creation, so the confirmation is read off what came back rather than
      // promising a queue nobody at that school looks at.
      //
      // Nothing came back at all means the request is in the outbox — the
      // prerequisites and the credit ceiling are checked when it lands, so
      // there is no registration to confirm yet.
      final count = written.isEmpty ? _picked.length : written.length;
      final courses = 'course${count == 1 ? '' : 's'}';
      final approval = written.any((row) => row['status'] != 'APPROVED');
      showApiMessage(
        context,
        written.isEmpty
            ? '$count $courses saved on this device. They will be sent when '
                  'there is a connection.'
            : approval
            ? '$count $courses sent for approval.'
            : '$count $courses registered.',
      );
      _reload();
    } on ApiError catch (e) {
      if (!mounted) return;
      // REGISTRATION_REJECTED carries a row per course that failed, and that
      // list is the whole answer to "why not" — a snackbar saying "some
      // courses could not be registered" is a support call.
      final failures = e.details['rows'];
      if (e.code == 'REGISTRATION_REJECTED' && failures is List) {
        await showDialog<void>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('Nothing was registered'),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(e.message),
                const SizedBox(height: 12),
                for (final row in failures.cast<Map<String, dynamic>>())
                  Text('• ${row['subject']} — ${row['message']}'),
              ],
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('Close'),
              ),
            ],
          ),
        );
      } else {
        showApiMessage(context, e.message);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final open = _term?['accepts_registration'] == true;
    return Scaffold(
      appBar: AppBar(title: const Text('Course registration')),
      body: Column(
        children: [
          if (_busy) const LinearProgressIndicator(),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              children: [
                if (_terms.isNotEmpty)
                  DropdownButtonFormField<String>(
                    initialValue: _term?['id'] as String?,
                    isExpanded: true,
                    decoration: InputDecoration(
                      labelText: widget.session.label('TERM'),
                    ),
                    items: [
                      for (final term in _terms)
                        DropdownMenuItem(
                          value: term['id'] as String,
                          child: Text(termLabel(term)),
                        ),
                    ],
                    onChanged: _busy
                        ? null
                        : (value) {
                            setState(
                              () => _term = _terms.firstWhere(
                                (term) => term['id'] == value,
                              ),
                            );
                            _reload();
                          },
                  ),
                const SizedBox(height: 8),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.person_search_outlined),
                  title: Text(
                    _student?['full_name'] as String? ?? 'Choose a student',
                  ),
                  subtitle: Text(
                    _student?['display_number'] as String? ??
                        'Search by name or admission number',
                  ),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: _busy ? null : _pickStudent,
                ),
                // The server refuses a closed term outright, so say it here
                // rather than after eight ticks and a round trip.
                if (_term != null && !open)
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Text(
                      'Registration is closed for this '
                      '${widget.session.label('TERM').toLowerCase()}.',
                      style: text.bodySmall?.copyWith(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
                  ),
              ],
            ),
          ),
          Expanded(
            child: _student == null || _term == null
                ? const EmptyState(
                    icon: Icons.playlist_add_outlined,
                    message: 'Choose a student to see their courses.',
                  )
                : AsyncView<_Sheet>(
                    key: _key,
                    load: _load,
                    builder: (context, sheet) => sheet.enrolment == null
                        ? const EmptyState(
                            icon: Icons.person_off_outlined,
                            message:
                                'This student has no enrolment in that '
                                'session, so there is nothing to register '
                                'against.',
                          )
                        // The bar lives in here rather than beside the
                        // AsyncView because it needs the loaded sheet: what is
                        // ticked is only ever a subset of what is on screen.
                        : Column(
                            children: [
                              Expanded(
                                child: _CourseList(
                                  sheet: sheet,
                                  picked: _picked,
                                  enabled: open && !_busy,
                                  onSearch: (value) {
                                    setState(() => _courseSearch = value);
                                    _key.currentState?.reload();
                                  },
                                  onToggle: (subject, on) => setState(
                                    () => on
                                        ? _picked[subject['id'] as String] =
                                              subject
                                        : _picked.remove(subject['id']),
                                  ),
                                ),
                              ),
                              if (_picked.isNotEmpty)
                                _SubmitBar(
                                  count: _picked.length,
                                  busy: _busy,
                                  units: totalUnits(_picked.values),
                                  onSubmit: () => _submit(sheet),
                                ),
                            ],
                          ),
                  ),
          ),
        ],
      ),
    );
  }
}

class _CourseList extends StatelessWidget {
  const _CourseList({
    required this.sheet,
    required this.picked,
    required this.enabled,
    required this.onSearch,
    required this.onToggle,
  });

  final _Sheet sheet;
  final Map<String, Map<String, dynamic>> picked;
  final bool enabled;
  final void Function(String value) onSearch;
  final void Function(Map<String, dynamic> subject, bool on) onToggle;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
          child: TextField(
            decoration: const InputDecoration(
              hintText: 'Course code or title',
              prefixIcon: Icon(Icons.search),
              isDense: true,
              border: OutlineInputBorder(),
            ),
            textInputAction: TextInputAction.search,
            // On submit, like the student list: a tertiary catalogue is
            // several hundred courses and this network does not spare a round
            // trip per keystroke.
            onSubmitted: (value) => onSearch(value.trim()),
          ),
        ),
        Expanded(
          child: sheet.subjects.isEmpty
              ? const EmptyState(
                  icon: Icons.menu_book_outlined,
                  message: 'No courses match this search.',
                )
              : ListView.builder(
                  itemCount: sheet.subjects.length,
                  itemBuilder: (context, index) {
                    final subject = sheet.subjects[index];
                    final id = subject['id'] as String;
                    final status = sheet.existing[id]?['status'] as String?;
                    final pickable = canPick(status);
                    return CheckboxListTile(
                      dense: true,
                      value: picked.containsKey(id),
                      onChanged: enabled && pickable
                          ? (on) => onToggle(subject, on ?? false)
                          : null,
                      title: Text(
                        '${subject['code'] ?? ''} · ${subject['title'] ?? ''}',
                      ),
                      subtitle: Text(
                        [
                          '${subject['credit_units'] ?? 0} units',
                          if (status != null) _statusLabel[status] ?? status,
                        ].join(' · '),
                        style: status != null ? text.bodySmall : null,
                      ),
                    );
                  },
                ),
        ),
      ],
    );
  }
}

class _SubmitBar extends StatelessWidget {
  const _SubmitBar({
    required this.count,
    required this.units,
    required this.busy,
    required this.onSubmit,
  });

  final int count;
  final int units;
  final bool busy;
  final Future<void> Function() onSubmit;

  @override
  Widget build(BuildContext context) => Material(
    elevation: 3,
    child: Padding(
      padding: const EdgeInsets.all(12),
      child: Row(
        children: [
          Expanded(
            child: Text(
              '$count course${count == 1 ? '' : 's'} · $units units',
            ),
          ),
          FilledButton(
            onPressed: busy ? null : onSubmit,
            child: const Text('Register'),
          ),
        ],
      ),
    ),
  );
}

/// Search the student register and pick one. A dialog rather than a pane: the
/// screen is used once per student and the list is only in the way afterwards.
class _StudentPicker extends StatefulWidget {
  const _StudentPicker({required this.repo});

  final Repository repo;

  @override
  State<_StudentPicker> createState() => _StudentPickerState();
}

class _StudentPickerState extends State<_StudentPicker> {
  final _key = GlobalKey<AsyncViewState<List<Map<String, dynamic>>>>();
  String _search = '';

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Choose a student'),
    content: SizedBox(
      width: 400,
      height: 360,
      child: Column(
        children: [
          TextField(
            autofocus: true,
            decoration: const InputDecoration(
              hintText: 'Name or admission number',
              prefixIcon: Icon(Icons.search),
              isDense: true,
              border: OutlineInputBorder(),
            ),
            textInputAction: TextInputAction.search,
            onSubmitted: (value) {
              setState(() => _search = value.trim());
              _key.currentState?.reload();
            },
          ),
          const SizedBox(height: 8),
          Expanded(
            child: AsyncView<List<Map<String, dynamic>>>(
              key: _key,
              load: () => widget.repo.students(
                status: 'ACTIVE',
                search: _search.isEmpty ? null : _search,
              ),
              empty: const EmptyState(
                icon: Icons.school_outlined,
                message: 'No students match that search.',
              ),
              builder: (context, students) => ListView.builder(
                itemCount: students.length,
                itemBuilder: (context, index) {
                  final student = students[index];
                  return ListTile(
                    dense: true,
                    title: Text(student['full_name'] as String? ?? ''),
                    subtitle: Text(
                      '${student['display_number'] ?? student['admission_number'] ?? ''}',
                    ),
                    onTap: () => Navigator.pop(context, student),
                  );
                },
              ),
            ),
          ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
    ],
  );
}

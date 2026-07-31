import 'package:flutter/material.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';

/// The school's calendar, its year groups, and the streams it splits its
/// seniors into.
///
/// Everything academic keys off a level: a class belongs to one, an enrolment
/// records one, a promotion run walks from one to the next. A school is seeded
/// with the six Nigerian levels (or five tertiary ones) and most never touch
/// them again — but the one that runs a nursery arm, or renames JSS to Basic,
/// had to open Django admin to do it, which is a login nobody in a school
/// office has.
///
/// The calendar had the same gap and a worse consequence: no session or term is
/// seeded (`academics/seeds.py` — "those carry real dates only the school
/// knows"), and `set-current` was an endpoint with no caller, so the principal
/// who holds `academics.manage_structure` still had no way to advance a term.
/// Every screen that reads the current term — the mark sheet, the register,
/// registration, the broadsheet — was pinned to whatever was keyed last.
///
/// All three tables in one screen because they are the same size and the same
/// job: a page of reference data, keyed at setup and touched once a term.
/// Three screens would be three dashboard tiles for the same visit.
class StructureScreen extends StatefulWidget {
  const StructureScreen({super.key, required this.session});

  final Session session;

  @override
  State<StructureScreen> createState() => _StructureScreenState();
}

/// The write body for a level, built from what the form holds.
///
/// A blank name falls back to the code: a school keying "NUR1" means the level
/// is called NUR1, and refusing the save over a field it left alone because it
/// had already said the answer is a refusal nobody can act on.
///
/// `next_level` is cleared by `is_terminal`, because a final year promotes
/// nobody — a row saying both graduate and go on to SSS3 is a promotion run
/// that does one of them, and which one is an implementation detail.
Map<String, dynamic> levelPayload({
  required String code,
  required String name,
  String order = '',
  String? nextLevel,
  bool isTerminal = false,
  int fallbackOrder = 1,
}) {
  // "jss1" and "JSS1" as two levels is a school split in half on every
  // report, and the code is what people match on.
  final normalised = code.trim().toUpperCase();
  return {
    'code': normalised,
    'name': name.trim().isEmpty ? normalised : name.trim(),
    'order': int.tryParse(order.trim()) ?? fallbackOrder,
    'next_level': isTerminal ? null : nextLevel,
    'is_terminal': isTerminal,
  };
}

/// The write body for a stream.
///
/// The code is a slug the server validates, and deriving it from the name is
/// what keeps a school from being asked for it: nobody in an office has an
/// opinion about "science" versus "Science", and the one who types the second
/// gets a refusal for it. [code] is passed only when editing, where the stream
/// already has one that subjects and classes are pointing at.
Map<String, dynamic> streamPayload({required String name, String? code}) => {
  'code': code ?? slug(name),
  'name': name.trim(),
};

/// Lower case, hyphens for anything else, no hyphen at either end.
String slug(String value) => value
    .trim()
    .toLowerCase()
    .replaceAll(RegExp(r'[^a-z0-9]+'), '-')
    .replaceAll(RegExp(r'^-+|-+$'), '');

/// `YYYY-MM-DD`, which is what both date fields on the server take.
String isoDate(DateTime day) => day.toIso8601String().split('T').first;

/// "1 Sep 2025" — short enough for a subtitle that carries two of them.
String shortDate(DateTime? day) =>
    day == null ? '—' : '${day.day} ${_months[day.month - 1]} ${day.year}';

const _months = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

const _ordinals = ['First', 'Second', 'Third', 'Fourth'];

/// "Second Term" at a school, "Second Semester" at a polytechnic. The label
/// comes from the tenant, so nothing here decides which word a school uses.
String termName(int index, String termLabel) {
  final ordinal = index >= 1 && index <= _ordinals.length
      ? _ordinals[index - 1]
      : '$index';
  return '$ordinal $termLabel';
}

/// The write body for a session. Dates come off a picker, so the only thing
/// that can be wrong here is the name.
Map<String, dynamic> sessionPayload({
  required String name,
  required DateTime start,
  required DateTime end,
}) => {
  'name': name.trim(),
  'start_date': isoDate(start),
  'end_date': isoDate(end),
};

/// The write body for a term. A blank name falls back to the ordinal, the same
/// way a level's falls back to its code: a school that keyed the dates has
/// already said which term this is.
Map<String, dynamic> termPayload({
  required String session,
  required int index,
  required String name,
  required DateTime start,
  required DateTime end,
  String termLabel = 'Term',
}) => {
  'session': session,
  'index': index,
  'name': name.trim().isEmpty ? termName(index, termLabel) : name.trim(),
  'start_date': isoDate(start),
  'end_date': isoDate(end),
};

/// The row whose `is_current` is true, or null. The server keeps exactly one
/// of each, and keeps the term inside the session — see `AcademicSession.save`.
Map<String, dynamic>? currentRow(List<Map<String, dynamic>> rows) {
  for (final row in rows) {
    if (row['is_current'] == true) return row;
  }
  return null;
}

Map<String, dynamic>? rowById(List<Map<String, dynamic>> rows, String? id) {
  if (id == null) return null;
  for (final row in rows) {
    if (row['id'] == id) return row;
  }
  return null;
}

/// Which year's terms to show: the one picked, if it is still there, else
/// whichever is current.
///
/// Falling back rather than showing an empty list matters because the picked
/// year can vanish under the screen — another office deletes a mis-keyed
/// session, or this phone was offline while it happened.
Map<String, dynamic>? sessionInView(
  List<Map<String, dynamic>> sessions,
  String? picked,
) => rowById(sessions, picked) ?? currentRow(sessions);

/// Every reference list in one load: they are one screen and one refresh.
class Structure {
  const Structure({
    required this.sessions,
    required this.viewedSession,
    required this.terms,
    required this.levels,
    required this.streams,
  });

  final List<Map<String, dynamic>> sessions;

  /// The year [terms] belongs to — the picked one, or the current one.
  final Map<String, dynamic>? viewedSession;

  /// One session's terms. Every term the school has ever run is a list that
  /// grows by three a year and reads as one flat blur; a term only means
  /// anything inside its year.
  final List<Map<String, dynamic>> terms;
  final List<Map<String, dynamic>> levels;
  final List<Map<String, dynamic>> streams;
}

class _StructureScreenState extends State<StructureScreen> {
  final _key = GlobalKey<AsyncViewState<Structure>>();
  late final Repository _repo = Repository(widget.session.api);

  bool get _canEdit => widget.session.can('academics.manage_structure');

  String get _levelLabel => widget.session.label('CLASS');
  String get _sessionLabel => widget.session.label('SESSION');
  String get _termLabel => widget.session.label('TERM');

  /// The year whose terms are on show, or null for "whichever is current".
  ///
  /// Null rather than the current session's id, so that rolling the year over
  /// moves the list with the school instead of pinning it to the year that was
  /// current when the screen opened.
  String? _picked;

  /// The terms load depends on which session is in view, so it is a second
  /// round trip rather than a fourth parallel one.
  Future<Structure> _load() async {
    final lists = await Future.wait([
      _repo.sessions(),
      _repo.classLevels(),
      _repo.streams(),
    ]);
    final sessions = lists[0];
    final viewed = sessionInView(sessions, _picked);
    return Structure(
      sessions: sessions,
      viewedSession: viewed,
      terms: viewed == null
          ? const []
          : await _repo.terms(session: viewed['id'] as String),
      levels: lists[1],
      streams: lists[2],
    );
  }

  void _reload() => _key.currentState?.reload();

  Future<void> _editSession([Map<String, dynamic>? session]) async {
    final changed = await showDialog<bool>(
      context: context,
      builder: (context) => _SessionDialog(
        repo: _repo,
        session: session,
        sessionLabel: _sessionLabel,
      ),
    );
    if (changed == true) _reload();
  }

  Future<void> _editTerm(
    Structure structure, [
    Map<String, dynamic>? term,
  ]) async {
    // The year on show, not the current one: a term keyed in advance belongs to
    // the year being set up, and that is the whole point of the picker.
    final session = structure.viewedSession;
    if (session == null) return;
    final changed = await showDialog<bool>(
      context: context,
      builder: (context) => _TermDialog(
        repo: _repo,
        sessionId: session['id'] as String,
        sessionName: (session['name'] ?? '').toString(),
        terms: structure.terms,
        term: term,
        termLabel: _termLabel,
      ),
    );
    if (changed == true) _reload();
  }

  /// Making a session or a term current is not a field edit, so it does not go
  /// through the dialogs: it is one button, one confirmation naming what moves,
  /// and one call to the endpoint that keeps the pair consistent.
  Future<void> _makeCurrent({
    required String what,
    required String message,
    required Future<void> Function() call,
  }) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Move the school to $what?'),
        content: Text(message),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Move'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await call();
      if (mounted) showApiMessage(context, 'The school is now in $what.');
      _reload();
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    }
  }

  Future<void> _editLevel(
    List<Map<String, dynamic>> levels, [
    Map<String, dynamic>? level,
  ]) async {
    final changed = await showDialog<bool>(
      context: context,
      builder: (context) => _LevelDialog(
        repo: _repo,
        levels: levels,
        level: level,
        levelLabel: _levelLabel,
      ),
    );
    if (changed == true) _reload();
  }

  Future<void> _editStream([Map<String, dynamic>? stream]) async {
    final changed = await showDialog<bool>(
      context: context,
      builder: (context) => _StreamDialog(repo: _repo, stream: stream),
    );
    if (changed == true) _reload();
  }

  /// "Promoted to SSS1", read off the chain the levels themselves hold. The
  /// list is one page long, so the next level is looked up here rather than
  /// asked for expanded.
  String _promotion(
    Map<String, dynamic> level,
    List<Map<String, dynamic>> all,
  ) {
    if (level['is_terminal'] == true) return 'Final year — graduates';
    final next = level['next_level'];
    if (next == null) return 'Promotes nowhere yet';
    for (final row in all) {
      if (row['id'] == next) {
        return 'Promotes to ${(row['name'] ?? row['code']).toString()}';
      }
    }
    return 'Promotes to another ${_levelLabel.toLowerCase()}';
  }

  /// The calendar section: the years the school has run, and the terms of one
  /// of them — the current year unless another is picked.
  List<Widget> _calendar(Structure structure) {
    final current = currentRow(structure.sessions);
    final viewed = structure.viewedSession;
    final isViewingCurrent = viewed == null || viewed['id'] == current?['id'];
    return [
      _SectionHeader(
        title: '${_sessionLabel}s',
        subtitle: current == null
            ? 'None is current. Nothing can be enrolled, billed or marked.'
            : 'The school is in ${current['name']}',
        onAdd: _canEdit ? () => _editSession() : null,
        addLabel: 'Add ${_sessionLabel.toLowerCase()}',
      ),
      for (final session in structure.sessions)
        _PeriodTile(
          icon: Icons.calendar_month_outlined,
          title: (session['name'] ?? '').toString(),
          subtitle:
              '${shortDate(DateTime.tryParse(session['start_date'] as String? ?? ''))}'
              ' – '
              '${shortDate(DateTime.tryParse(session['end_date'] as String? ?? ''))}',
          isCurrent: session['is_current'] == true,
          onTap: _canEdit ? () => _editSession(session) : null,
          onMakeCurrent: _canEdit
              ? () => _makeCurrent(
                  what: (session['name'] ?? '').toString(),
                  message:
                      'Enrolment, class lists, fees and results all move to '
                      '${session['name']}.\n\n'
                      'The ${_termLabel.toLowerCase()} moves with it — whichever '
                      'one of its ${_termLabel.toLowerCase()}s covers today, or '
                      'its first. Nothing already recorded changes.',
                  call: () => _repo.setCurrentSession(session['id'] as String),
                )
              : null,
        ),
      const Divider(height: 32),
      _SectionHeader(
        title: '${_termLabel}s',
        subtitle: switch ((viewed, structure.terms.isEmpty)) {
          (null, _) =>
            'Add a ${_sessionLabel.toLowerCase()} first — a '
                '${_termLabel.toLowerCase()} belongs to one.',
          (_, true) =>
            '${viewed!['name']} has none yet. Marks and registration '
                'need one.',
          // The guard is only reachable with a year in view: the current year
          // is one, and so is a picked one.
          _ when !isViewingCurrent =>
            'In ${viewed['name']} — not the year the school is in',
          _ => 'In ${viewed!['name']}',
        },
        onAdd: _canEdit && viewed != null ? () => _editTerm(structure) : null,
        addLabel: 'Add ${_termLabel.toLowerCase()}',
      ),
      // One session is nothing to choose between, and the header already names
      // it.
      if (structure.sessions.length > 1)
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
          child: DropdownButtonFormField<String>(
            initialValue: viewed?['id'] as String?,
            isExpanded: true,
            decoration: InputDecoration(
              labelText: 'Showing',
              helperText:
                  'Key next year\'s ${_termLabel.toLowerCase()}s before it '
                  'starts, or fix a date in a year already closed.',
            ),
            items: [
              for (final session in structure.sessions)
                DropdownMenuItem(
                  value: session['id'] as String,
                  child: Text(
                    session['is_current'] == true
                        ? '${session['name']} (current)'
                        : (session['name'] ?? '').toString(),
                  ),
                ),
            ],
            onChanged: (value) {
              // The current year is stored as null, not as its own id: that is
              // what makes the list follow a rollover instead of staying on the
              // year that happened to be current a moment ago.
              setState(() => _picked = value == current?['id'] ? null : value);
              _reload();
            },
          ),
        ),
      for (final term in structure.terms)
        _PeriodTile(
          icon: Icons.event_note_outlined,
          title: (term['name'] ?? '').toString(),
          subtitle:
              '${shortDate(DateTime.tryParse(term['start_date'] as String? ?? ''))}'
              ' – '
              '${shortDate(DateTime.tryParse(term['end_date'] as String? ?? ''))}'
              '${term['results_published'] == true ? ' · results published' : ''}',
          isCurrent: term['is_current'] == true,
          onTap: _canEdit ? () => _editTerm(structure, term) : null,
          onMakeCurrent: _canEdit
              ? () => _makeCurrent(
                  what: (term['name'] ?? '').toString(),
                  message:
                      'Mark sheets, the register, course registration and the '
                      'timetable all move to ${term['name']}.\n\n'
                      // Advancing to a term of another year moves the year too
                      // — the server promotes the session that holds it — and
                      // that is a bigger move than the button suggests.
                      '${isViewingCurrent ? '' : 'The school moves into ${viewed['name']} with it: '
                                'enrolment, class lists and fees follow.\n\n'}'
                      'Marks already entered stay where they are.',
                  call: () => _repo.setCurrentTerm(term['id'] as String),
                )
              : null,
        ),
      const Divider(height: 32),
    ];
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(title: const Text('Academic setup')),
      body: AsyncView<Structure>(
        key: _key,
        load: _load,
        builder: (context, structure) => ListView(
          padding: const EdgeInsets.only(bottom: 32),
          children: [
            ..._calendar(structure),
            _SectionHeader(
              title: '${_levelLabel}s',
              subtitle: structure.levels.isEmpty
                  ? 'Nothing can be enrolled until one exists.'
                  : '${structure.levels.length} in order of promotion',
              onAdd: _canEdit ? () => _editLevel(structure.levels) : null,
              addLabel: 'Add ${_levelLabel.toLowerCase()}',
            ),
            for (final level in structure.levels)
              ListTile(
                leading: CircleAvatar(child: Text('${level['order'] ?? '?'}')),
                title: Text((level['name'] ?? level['code']).toString()),
                subtitle: Text(
                  '${level['code']} · ${_promotion(level, structure.levels)}',
                ),
                onTap: _canEdit
                    ? () => _editLevel(structure.levels, level)
                    : null,
              ),
            const Divider(height: 32),
            _SectionHeader(
              title: 'Streams',
              subtitle: structure.streams.isEmpty
                  ? 'A school that streams nobody needs none.'
                  : 'Senior secondary: Science, Arts, Commercial',
              onAdd: _canEdit ? () => _editStream() : null,
              addLabel: 'Add stream',
            ),
            for (final stream in structure.streams)
              ListTile(
                leading: const Icon(Icons.call_split_outlined),
                title: Text((stream['name'] ?? stream['code']).toString()),
                subtitle: Text(stream['code'].toString()),
                onTap: _canEdit ? () => _editStream(stream) : null,
              ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 24, 16, 0),
              child: Text(
                'A ${_levelLabel.toLowerCase()} anybody has ever been enrolled '
                'in cannot be deleted — the register, the marks and the '
                'promotion history all point at it. Rename it instead.',
                style: text.bodySmall,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader({
    required this.title,
    required this.subtitle,
    required this.addLabel,
    this.onAdd,
  });

  final String title;
  final String subtitle;
  final String addLabel;
  final VoidCallback? onAdd;

  @override
  Widget build(BuildContext context) => ListTile(
    title: Text(title, style: Theme.of(context).textTheme.titleMedium),
    subtitle: Text(subtitle),
    trailing: onAdd == null
        ? null
        : TextButton.icon(
            onPressed: onAdd,
            icon: const Icon(Icons.add),
            label: Text(addLabel),
          ),
  );
}

/// One session or one term: its dates, whether it is the current one, and the
/// button that makes it so.
class _PeriodTile extends StatelessWidget {
  const _PeriodTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.isCurrent,
    this.onTap,
    this.onMakeCurrent,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final bool isCurrent;
  final VoidCallback? onTap;
  final VoidCallback? onMakeCurrent;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return ListTile(
      leading: Icon(icon, color: isCurrent ? scheme.primary : null),
      title: Text(title),
      subtitle: Text(subtitle),
      // The chip and the button never both show: one of them would be asking
      // the school to move to where it already is.
      trailing: isCurrent
          ? Chip(
              label: const Text('Current'),
              visualDensity: VisualDensity.compact,
            )
          : onMakeCurrent == null
          ? null
          : TextButton(
              onPressed: onMakeCurrent,
              child: const Text('Make current'),
            ),
      onTap: onTap,
    );
  }
}

/// A date as a tappable row. Typed dates are the one thing the server now
/// refuses outright (a period that ends before it begins), so nothing here is
/// typed.
class _DateField extends StatelessWidget {
  const _DateField({
    required this.label,
    required this.value,
    required this.onPicked,
  });

  final String label;
  final DateTime value;
  final ValueChanged<DateTime> onPicked;

  @override
  Widget build(BuildContext context) => ListTile(
    contentPadding: EdgeInsets.zero,
    title: Text(label, style: Theme.of(context).textTheme.bodySmall),
    subtitle: Text(shortDate(value)),
    trailing: const Icon(Icons.edit_calendar_outlined),
    onTap: () async {
      final picked = await showDatePicker(
        context: context,
        initialDate: value,
        // A school keys next year in advance and back-fills the year it just
        // closed; both are inside this range and nothing else needs to be.
        firstDate: DateTime(value.year - 5),
        lastDate: DateTime(value.year + 5),
      );
      if (picked != null) onPicked(picked);
    },
  );
}

/// Add or edit one session. Pops true when something was written.
///
/// No delete: a session cascades to every enrolment, registration, mark and
/// invoice of that year, and the server refuses it with 409 once any of them
/// exists (`ProtectDependentsMixin`). A year keyed wrong is renamed.
class _SessionDialog extends StatefulWidget {
  const _SessionDialog({
    required this.repo,
    required this.sessionLabel,
    this.session,
  });

  final Repository repo;
  final String sessionLabel;
  final Map<String, dynamic>? session;

  @override
  State<_SessionDialog> createState() => _SessionDialogState();
}

class _SessionDialogState extends State<_SessionDialog> {
  late final _name = TextEditingController(
    text: widget.session?['name'] as String? ?? _suggestedName(),
  );
  late DateTime _start =
      DateTime.tryParse(widget.session?['start_date'] as String? ?? '') ??
      DateTime(DateTime.now().year, 9, 1);
  late DateTime _end =
      DateTime.tryParse(widget.session?['end_date'] as String? ?? '') ??
      DateTime(DateTime.now().year + 1, 7, 31);
  bool _saving = false;

  bool get _isNew => widget.session == null;

  /// "2025/2026" — the Nigerian academic year, which runs September to July.
  /// Before September the current year is still the one in progress.
  static String _suggestedName() {
    final now = DateTime.now();
    final first = now.month >= 9 ? now.year : now.year - 1;
    return '$first/${first + 1}';
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_name.text.trim().isEmpty) {
      showApiMessage(context, 'A name is required: 2025/2026.');
      return;
    }
    // The server refuses this too — it is checked here because a picker can
    // produce it in two taps and a round trip to be told so is a waste.
    if (!_end.isAfter(_start)) {
      showApiMessage(context, 'The end date must come after the start.');
      return;
    }
    final payload = sessionPayload(name: _name.text, start: _start, end: _end);

    setState(() => _saving = true);
    try {
      if (_isNew) {
        await widget.repo.createSession(payload);
      } else {
        await widget.repo.updateSession(
          widget.session!['id'] as String,
          payload,
        );
      }
      if (mounted) Navigator.pop(context, true);
    } on ApiError catch (e) {
      // A duplicate name is the common refusal, and the server names it.
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final label = widget.sessionLabel.toLowerCase();
    return AlertDialog(
      title: Text('${_isNew ? 'Add' : 'Edit'} $label'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              controller: _name,
              decoration: const InputDecoration(
                labelText: 'Name',
                helperText: '2025/2026',
              ),
              textInputAction: TextInputAction.done,
            ),
            _DateField(
              label: 'Starts',
              value: _start,
              onPicked: (day) => setState(() => _start = day),
            ),
            _DateField(
              label: 'Ends',
              value: _end,
              onPicked: (day) => setState(() => _end = day),
            ),
            if (_isNew)
              Text(
                'Adding it does not move the school into it. Use "Make '
                'current" when the year actually starts.',
                style: Theme.of(context).textTheme.bodySmall,
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: _saving ? null : () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: _saving ? null : _save,
          child: Text(_isNew ? 'Add' : 'Save'),
        ),
      ],
    );
  }
}

/// Add or edit one term of the current session.
///
/// The session is not a field: a term is added to the year the school is in,
/// and moving to another year is what the session list does. No delete, for
/// the reason a session has none — the marks of that term hang off it.
class _TermDialog extends StatefulWidget {
  const _TermDialog({
    required this.repo,
    required this.sessionId,
    required this.sessionName,
    required this.terms,
    required this.termLabel,
    this.term,
  });

  final Repository repo;
  final String sessionId;
  final String sessionName;
  final List<Map<String, dynamic>> terms;
  final String termLabel;
  final Map<String, dynamic>? term;

  @override
  State<_TermDialog> createState() => _TermDialogState();
}

class _TermDialogState extends State<_TermDialog> {
  late int _index = widget.term?['index'] as int? ?? _nextIndex();
  late final _name = TextEditingController(
    text: widget.term?['name'] as String? ?? '',
  );
  late DateTime _start =
      DateTime.tryParse(widget.term?['start_date'] as String? ?? '') ??
      DateTime.now();
  late DateTime _end =
      DateTime.tryParse(widget.term?['end_date'] as String? ?? '') ??
      DateTime.now().add(const Duration(days: 90));
  bool _saving = false;

  bool get _isNew => widget.term == null;

  /// The one after the last term keyed, which is the one a school adding a
  /// term is almost always adding. The server caps the index at 4 and refuses
  /// a duplicate within the session.
  int _nextIndex() {
    var highest = 0;
    for (final term in widget.terms) {
      final index = term['index'];
      if (index is int && index > highest) highest = index;
    }
    return highest + 1 > 4 ? 4 : highest + 1;
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_end.isAfter(_start)) {
      showApiMessage(context, 'The end date must come after the start.');
      return;
    }
    final payload = termPayload(
      session: widget.sessionId,
      index: _index,
      name: _name.text,
      start: _start,
      end: _end,
      termLabel: widget.termLabel,
    );

    setState(() => _saving = true);
    try {
      if (_isNew) {
        await widget.repo.createTerm(payload);
      } else {
        await widget.repo.updateTerm(widget.term!['id'] as String, payload);
      }
      if (mounted) Navigator.pop(context, true);
    } on ApiError catch (e) {
      // "unique_term_per_session" is the one that bites: two second terms.
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final label = widget.termLabel.toLowerCase();
    return AlertDialog(
      title: Text('${_isNew ? 'Add' : 'Edit'} $label'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'In ${widget.sessionName}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 8),
            DropdownButtonFormField<int>(
              initialValue: _index,
              decoration: InputDecoration(
                labelText: 'Which $label',
                helperText: 'The order marks and results are read in',
              ),
              items: [
                for (var index = 1; index <= 4; index++)
                  DropdownMenuItem(
                    value: index,
                    child: Text(termName(index, widget.termLabel)),
                  ),
              ],
              onChanged: (value) => setState(() => _index = value ?? _index),
            ),
            TextField(
              controller: _name,
              decoration: InputDecoration(
                labelText: 'Name',
                helperText:
                    'Leave blank for ${termName(_index, widget.termLabel)}',
              ),
              textInputAction: TextInputAction.done,
            ),
            _DateField(
              label: 'Starts',
              value: _start,
              onPicked: (day) => setState(() => _start = day),
            ),
            _DateField(
              label: 'Ends',
              value: _end,
              onPicked: (day) => setState(() => _end = day),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: _saving ? null : () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: _saving ? null : _save,
          child: Text(_isNew ? 'Add' : 'Save'),
        ),
      ],
    );
  }
}

/// Add or edit one level. Pops true when something was written.
class _LevelDialog extends StatefulWidget {
  const _LevelDialog({
    required this.repo,
    required this.levels,
    required this.levelLabel,
    this.level,
  });

  final Repository repo;
  final List<Map<String, dynamic>> levels;
  final String levelLabel;
  final Map<String, dynamic>? level;

  @override
  State<_LevelDialog> createState() => _LevelDialogState();
}

class _LevelDialogState extends State<_LevelDialog> {
  late final _code = TextEditingController(
    text: widget.level?['code'] as String? ?? '',
  );
  late final _name = TextEditingController(
    text: widget.level?['name'] as String? ?? '',
  );
  late final _order = TextEditingController(
    text: switch (widget.level?['order']) {
      null => '${_nextOrder()}',
      final order => '$order',
    },
  );
  late String? _nextLevel = widget.level?['next_level'] as String?;
  late bool _terminal = widget.level?['is_terminal'] == true;
  bool _saving = false;

  bool get _isNew => widget.level == null;

  /// A new level goes after the last one, which is where a school adding one
  /// almost always wants it — an extra senior year, not an extra nursery.
  int _nextOrder() {
    var highest = 0;
    for (final level in widget.levels) {
      final order = level['order'];
      if (order is int && order > highest) highest = order;
    }
    return highest + 1;
  }

  /// Nothing promotes to itself, and that is the only cycle worth blocking
  /// from here — a longer one is the server's to judge.
  List<DropdownMenuItem<String?>> get _nextItems => [
    const DropdownMenuItem(value: null, child: Text('Nowhere yet')),
    for (final level in widget.levels)
      if (level['id'] != widget.level?['id'])
        DropdownMenuItem(
          value: level['id'] as String,
          child: Text((level['name'] ?? level['code']).toString()),
        ),
  ];

  @override
  void dispose() {
    _code.dispose();
    _name.dispose();
    _order.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_code.text.trim().isEmpty) {
      showApiMessage(context, 'A code is required: JSS1, SSS3, 200.');
      return;
    }
    final payload = levelPayload(
      code: _code.text,
      name: _name.text,
      order: _order.text,
      nextLevel: _nextLevel,
      isTerminal: _terminal,
      fallbackOrder: _nextOrder(),
    );

    setState(() => _saving = true);
    try {
      if (_isNew) {
        await widget.repo.createClassLevel(payload);
      } else {
        await widget.repo.updateClassLevel(
          widget.level!['id'] as String,
          payload,
        );
      }
      if (mounted) Navigator.pop(context, true);
    } on ApiError catch (e) {
      // A duplicate code is the common refusal, and the server names it.
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _delete() async {
    final label = widget.levelLabel.toLowerCase();
    final deleted = await confirmDelete(
      context,
      title: 'Delete this $label?',
      message:
          '${_code.text} is removed from the school.\n\n'
          'Its classes go with it. A $label anybody has ever been enrolled in '
          'is refused outright — rename it instead.',
      onConfirm: () =>
          widget.repo.deleteClassLevel(widget.level!['id'] as String),
    );
    if (deleted && mounted) Navigator.pop(context, true);
  }

  @override
  Widget build(BuildContext context) {
    final label = widget.levelLabel.toLowerCase();
    return AlertDialog(
      title: Text('${_isNew ? 'Add' : 'Edit'} $label'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              controller: _code,
              decoration: const InputDecoration(
                labelText: 'Code',
                helperText: 'JSS1, SSS3, 200',
              ),
              textCapitalization: TextCapitalization.characters,
              textInputAction: TextInputAction.next,
            ),
            TextField(
              controller: _name,
              decoration: const InputDecoration(
                labelText: 'Name',
                helperText: 'Leave blank to use the code',
              ),
              textInputAction: TextInputAction.next,
            ),
            TextField(
              controller: _order,
              decoration: const InputDecoration(
                labelText: 'Order',
                helperText: '1 is the first year of the school',
              ),
              keyboardType: TextInputType.number,
            ),
            const SizedBox(height: 8),
            // Hidden on a final year: it is the field the switch just cleared,
            // and offering it back is a question with no valid answer.
            if (!_terminal)
              DropdownButtonFormField<String?>(
                initialValue: _nextLevel,
                isExpanded: true,
                decoration: const InputDecoration(
                  labelText: 'Promotes to',
                  helperText: 'Where a promotion run sends this year group',
                ),
                items: _nextItems,
                onChanged: (value) => setState(() => _nextLevel = value),
              ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Final year'),
              subtitle: const Text('Graduates rather than promotes'),
              value: _terminal,
              onChanged: (value) => setState(() => _terminal = value),
            ),
          ],
        ),
      ),
      actions: [
        if (!_isNew)
          TextButton.icon(
            onPressed: _saving ? null : _delete,
            icon: const Icon(Icons.delete_outline),
            label: const Text('Delete'),
          ),
        TextButton(
          onPressed: _saving ? null : () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: _saving ? null : _save,
          child: Text(_isNew ? 'Add' : 'Save'),
        ),
      ],
    );
  }
}

/// Add or edit one stream. Two fields on the model, one on the form: the code
/// is derived from the name when the stream is created and never touched
/// again, because subjects and classes are pointing at it by then.
class _StreamDialog extends StatefulWidget {
  const _StreamDialog({required this.repo, this.stream});

  final Repository repo;
  final Map<String, dynamic>? stream;

  @override
  State<_StreamDialog> createState() => _StreamDialogState();
}

class _StreamDialogState extends State<_StreamDialog> {
  late final _name = TextEditingController(
    text: widget.stream?['name'] as String? ?? '',
  );
  bool _saving = false;

  bool get _isNew => widget.stream == null;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (slug(_name.text).isEmpty) {
      showApiMessage(context, 'A name is required: Science, Arts.');
      return;
    }
    final payload = streamPayload(
      name: _name.text,
      code: widget.stream?['code'] as String?,
    );

    setState(() => _saving = true);
    try {
      if (_isNew) {
        await widget.repo.createStream(payload);
      } else {
        await widget.repo.updateStream(widget.stream!['id'] as String, payload);
      }
      if (mounted) Navigator.pop(context, true);
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _delete() async {
    final deleted = await confirmDelete(
      context,
      title: 'Delete this stream?',
      message:
          '${_name.text} is removed from the school.\n\n'
          'Classes and subjects that named it keep working — they simply stop '
          'being streamed. Students who offer it lose the setting.',
      onConfirm: () => widget.repo.deleteStream(widget.stream!['id'] as String),
    );
    if (deleted && mounted) Navigator.pop(context, true);
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text('${_isNew ? 'Add' : 'Edit'} stream'),
    content: TextField(
      controller: _name,
      autofocus: true,
      decoration: const InputDecoration(
        labelText: 'Name',
        helperText: 'Science, Arts, Commercial',
      ),
      textInputAction: TextInputAction.done,
      onSubmitted: (_) => _save(),
    ),
    actions: [
      if (!_isNew)
        TextButton.icon(
          onPressed: _saving ? null : _delete,
          icon: const Icon(Icons.delete_outline),
          label: const Text('Delete'),
        ),
      TextButton(
        onPressed: _saving ? null : () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: _saving ? null : _save,
        child: Text(_isNew ? 'Add' : 'Save'),
      ),
    ],
  );
}

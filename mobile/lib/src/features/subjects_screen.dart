import 'package:flutter/material.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';

/// The subject catalogue: what the school teaches, and to whom.
///
/// Everything downstream hangs off a row here — a teaching assignment, a
/// timetable period, a registration, a mark sheet column — so until now the
/// catalogue could only be built in Django admin, which nobody in a school
/// office has a login for.
///
/// Reads are open to anyone signed in (`SubjectViewSet` allows it: a timetable
/// is unreadable without subject titles). Writing needs
/// `academics.manage_structure`, and the server enforces it again.
class SubjectsScreen extends StatelessWidget {
  const SubjectsScreen({super.key, required this.session});

  final Session session;

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Subjects')),
    body: _SubjectList(session: session),
  );
}

const subjectCategories = {
  'COMPULSORY': 'Compulsory',
  'ELECTIVE': 'Elective',
  'GENERAL': 'General studies',
};

/// The write body for a subject, built from what the form holds.
///
/// `credit_units` is 0 for a secondary subject — weighting there comes from
/// the assessment components, not from units — so an empty box means zero
/// rather than a refusal. `level`, `department` and `stream` null are all
/// deliberate and all common: a subject taught across every year, by no
/// department, to every stream, is the ordinary secondary case.
///
/// `prerequisites` is the whole set, not a delta — the serializer replaces the
/// relation with what it is sent, so an empty list is how the last one is
/// removed.
Map<String, dynamic> subjectPayload({
  required String code,
  required String title,
  String category = 'COMPULSORY',
  String creditUnits = '',
  String? level,
  String? department,
  String? stream,
  List<String> prerequisites = const [],
  bool isActive = true,
}) => {
  // Codes are matched by people reading a timetable, and MTH101 and mth101
  // being two rows is a mark sheet split in half.
  'code': code.trim().toUpperCase(),
  'title': title.trim(),
  'category': category,
  'credit_units': int.tryParse(creditUnits.trim()) ?? 0,
  'level': level,
  'department': department,
  'stream': stream,
  'prerequisites': prerequisites,
  'is_active': isActive,
};

class _SubjectList extends StatefulWidget {
  const _SubjectList({required this.session});

  final Session session;

  @override
  State<_SubjectList> createState() => _SubjectListState();
}

class _SubjectListState extends State<_SubjectList> {
  final _key = GlobalKey<AsyncViewState<List<Map<String, dynamic>>>>();
  late final Repository _repo = Repository(widget.session.api);
  List<Map<String, dynamic>> _levels = const [];
  List<Map<String, dynamic>> _streams = const [];
  List<Map<String, dynamic>> _departments = const [];
  String _search = '';
  String? _category;
  bool? _active = true;

  bool get _canManage => widget.session.can('academics.manage_structure');

  @override
  void initState() {
    super.initState();
    _loadPickers();
  }

  /// The three reference lists the form picks from. A failure here is not a
  /// failure of the screen: all three fields are optional on a subject, so the
  /// form still saves without any of them.
  ///
  /// Which of them come back empty is also what decides whether the field is
  /// shown at all — a secondary school has no departments and a tertiary
  /// institution no streams, and neither should be offered a picker with
  /// nothing in it. That reads the school's own data rather than an
  /// institution-type flag, so a school that keeps both gets both.
  Future<void> _loadPickers() async {
    try {
      final lists = await Future.wait([
        _repo.classLevels(),
        _repo.streams(),
        _repo.departments(),
      ]);
      if (!mounted) return;
      setState(() {
        _levels = lists[0];
        _streams = lists[1];
        _departments = lists[2];
      });
    } on ApiError {
      // Leave the pickers empty.
    }
  }

  void _reload() => _key.currentState?.reload();

  Future<void> _edit([Map<String, dynamic>? subject]) async {
    final changed = await showDialog<bool>(
      context: context,
      builder: (context) => _SubjectDialog(
        repo: _repo,
        levels: _levels,
        streams: _streams,
        departments: _departments,
        subject: subject,
      ),
    );
    if (changed == true) _reload();
  }

  /// The list serialises these as bare ids, so the caption looks them up in
  /// the pickers already loaded rather than asking the server to expand them.
  String _nameIn(List<Map<String, dynamic>> rows, Object? id) {
    if (id == null) return '';
    for (final row in rows) {
      if (row['id'] == id) return (row['code'] ?? row['name'] ?? '').toString();
    }
    return '';
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    floatingActionButton: _canManage
        ? FloatingActionButton.extended(
            onPressed: () => _edit(),
            icon: const Icon(Icons.add),
            label: const Text('Add subject'),
          )
        : null,
    body: Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: TextField(
            decoration: const InputDecoration(
              hintText: 'Code or title',
              prefixIcon: Icon(Icons.search),
              isDense: true,
              border: OutlineInputBorder(),
            ),
            textInputAction: TextInputAction.search,
            // On submit, not per keystroke: each search is a round trip.
            onSubmitted: (value) {
              setState(() => _search = value.trim());
              _reload();
            },
          ),
        ),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Row(
            children: [
              for (final entry in {'': 'All', ...subjectCategories}.entries)
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                  child: ChoiceChip(
                    label: Text(entry.value),
                    selected: (_category ?? '') == entry.key,
                    onSelected: (_) {
                      setState(
                        () => _category = entry.key.isEmpty ? null : entry.key,
                      );
                      _reload();
                    },
                  ),
                ),
              const SizedBox(width: 12),
              // Retired subjects are off by default: the catalogue a school
              // works from is the one it currently teaches.
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 4),
                child: FilterChip(
                  label: const Text('Show retired'),
                  selected: _active == null,
                  onSelected: (on) {
                    setState(() => _active = on ? null : true);
                    _reload();
                  },
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: AsyncView<List<Map<String, dynamic>>>(
            key: _key,
            load: () => _repo.subjects(
              category: _category,
              active: _active,
              search: _search.isEmpty ? null : _search,
            ),
            empty: const EmptyState(
              icon: Icons.menu_book_outlined,
              message: 'No subjects match this filter.',
            ),
            builder: (context, subjects) => ListView.separated(
              itemCount: subjects.length,
              separatorBuilder: (_, _) => const Divider(height: 1),
              itemBuilder: (context, index) {
                final row = subjects[index];
                final units = row['credit_units'] as int? ?? 0;
                final level = _nameIn(_levels, row['level']);
                final stream = _nameIn(_streams, row['stream']);
                final department = _nameIn(_departments, row['department']);
                return ListTile(
                  title: Text(
                    '${row['code']} — ${row['title']}',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                  subtitle: Text(
                    [
                      subjectCategories[row['category']] ?? row['category'],
                      if (units > 0) '$units units',
                      if (level.isNotEmpty) level,
                      if (stream.isNotEmpty) stream,
                      if (department.isNotEmpty) department,
                    ].join(' · '),
                  ),
                  trailing: row['is_active'] == false
                      ? const Chip(
                          label: Text('Retired'),
                          visualDensity: VisualDensity.compact,
                        )
                      : null,
                  onTap: _canManage ? () => _edit(row) : null,
                );
              },
            ),
          ),
        ),
      ],
    ),
  );
}

/// One dialog for both jobs: `subject` null adds, otherwise it edits. The two
/// forms are the same fields against the same serializer, and a second widget
/// would only be the first one with a different verb on the button.
class _SubjectDialog extends StatefulWidget {
  const _SubjectDialog({
    required this.repo,
    required this.levels,
    required this.streams,
    required this.departments,
    this.subject,
  });

  final Repository repo;
  final List<Map<String, dynamic>> levels;
  final List<Map<String, dynamic>> streams;
  final List<Map<String, dynamic>> departments;
  final Map<String, dynamic>? subject;

  @override
  State<_SubjectDialog> createState() => _SubjectDialogState();
}

class _SubjectDialogState extends State<_SubjectDialog> {
  late final _code = TextEditingController(
    text: widget.subject?['code'] as String? ?? '',
  );
  late final _title = TextEditingController(
    text: widget.subject?['title'] as String? ?? '',
  );
  late final _units = TextEditingController(
    text: switch (widget.subject?['credit_units']) {
      null || 0 => '',
      final units => '$units',
    },
  );
  late String _category =
      widget.subject?['category'] as String? ?? 'COMPULSORY';
  late String? _level = widget.subject?['level'] as String?;
  late String? _stream = widget.subject?['stream'] as String?;
  late String? _department = widget.subject?['department'] as String?;
  late final Set<String> _prerequisites = {
    ...?(widget.subject?['prerequisites'] as List?)?.cast<String>(),
  };
  late bool _active = widget.subject?['is_active'] != false;
  List<Map<String, dynamic>> _catalogue = const [];
  bool _saving = false;

  bool get _isNew => widget.subject == null;

  @override
  void initState() {
    super.initState();
    _loadCatalogue();
  }

  /// The catalogue a prerequisite is chosen from. Loaded here rather than
  /// inside the picker, because the line on the form has to name the courses
  /// already saved against this one, and the row only carries their ids.
  ///
  /// Retired subjects are included: a course whose prerequisite stopped being
  /// taught still had it, and filtering them out would quietly drop one from
  /// the set on the next save. This subject is not — nothing is its own
  /// prerequisite, and that is the only cycle worth blocking from here.
  Future<void> _loadCatalogue() async {
    try {
      final subjects = await widget.repo.subjects();
      if (!mounted) return;
      setState(() {
        _catalogue = [
          for (final subject in subjects)
            if (subject['id'] != widget.subject?['id']) subject,
        ];
      });
    } on ApiError {
      // No picker rather than no form: every other field still saves, and the
      // prerequisites already stored go back untouched.
    }
  }

  String get _prerequisiteCaption {
    if (_prerequisites.isEmpty) return 'None';
    final codes = [
      for (final subject in _catalogue)
        if (_prerequisites.contains(subject['id'])) subject['code'].toString(),
    ];
    // Before the catalogue lands there are no codes to show, only a count.
    return codes.isEmpty
        ? '${_prerequisites.length} selected'
        : codes.join(', ');
  }

  Future<void> _pickPrerequisites() async {
    final picked = await showDialog<List<String>>(
      context: context,
      builder: (context) => _PrerequisiteDialog(
        catalogue: _catalogue,
        selected: _prerequisites.toList(),
      ),
    );
    if (picked == null) return;
    setState(() {
      _prerequisites
        ..clear()
        ..addAll(picked);
    });
  }

  @override
  void dispose() {
    _code.dispose();
    _title.dispose();
    _units.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_code.text.trim().isEmpty || _title.text.trim().isEmpty) {
      showApiMessage(context, 'A code and a title are required.');
      return;
    }
    final payload = subjectPayload(
      code: _code.text,
      title: _title.text,
      category: _category,
      creditUnits: _units.text,
      level: _level,
      department: _department,
      stream: _stream,
      prerequisites: _prerequisites.toList(),
      isActive: _active,
    );

    setState(() => _saving = true);
    try {
      if (_isNew) {
        await widget.repo.createSubject(payload);
      } else {
        await widget.repo.updateSubject(
          widget.subject!['id'] as String,
          payload,
        );
      }
      if (mounted) Navigator.pop(context, true);
    } on ApiError catch (e) {
      // A duplicate code is the common one, and the server's message names it.
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _delete() async {
    final deleted = await confirmDelete(
      context,
      title: 'Delete this subject?',
      message:
          '${_code.text} is removed from the catalogue.\n\n'
          'If it has ever been taught, untick "Currently taught" instead — '
          'that keeps the marks, registrations and timetable already recorded '
          'against it, and takes it out of every picker.',
      onConfirm: () =>
          widget.repo.deleteSubject(widget.subject!['id'] as String),
    );
    if (deleted && mounted) Navigator.pop(context, true);
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(_isNew ? 'Add subject' : 'Edit subject'),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: _code,
            decoration: const InputDecoration(
              labelText: 'Code',
              helperText: 'MTH or CSC201',
            ),
            textCapitalization: TextCapitalization.characters,
            textInputAction: TextInputAction.next,
          ),
          TextField(
            controller: _title,
            decoration: const InputDecoration(labelText: 'Title'),
            textInputAction: TextInputAction.next,
          ),
          TextField(
            controller: _units,
            decoration: const InputDecoration(
              labelText: 'Credit units',
              helperText: 'Leave blank for a secondary subject',
            ),
            keyboardType: TextInputType.number,
          ),
          const SizedBox(height: 16),
          DropdownButtonFormField<String?>(
            initialValue: _category,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Category'),
            items: [
              for (final entry in subjectCategories.entries)
                DropdownMenuItem(value: entry.key, child: Text(entry.value)),
            ],
            onChanged: (value) =>
                setState(() => _category = value ?? 'COMPULSORY'),
          ),
          const SizedBox(height: 8),
          DropdownButtonFormField<String?>(
            initialValue: _level,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Level'),
            items: [
              const DropdownMenuItem(value: null, child: Text('All levels')),
              for (final level in widget.levels)
                DropdownMenuItem(
                  value: level['id'] as String,
                  child: Text((level['name'] ?? level['code']).toString()),
                ),
            ],
            onChanged: (value) => setState(() => _level = value),
          ),
          // Both hidden when the school keeps none: an empty picker is a
          // question with one answer, and it is already the default.
          if (widget.streams.isNotEmpty) ...[
            const SizedBox(height: 8),
            DropdownButtonFormField<String?>(
              initialValue: _stream,
              isExpanded: true,
              decoration: const InputDecoration(
                labelText: 'Stream',
                helperText: 'Senior secondary: Science, Arts, Commercial',
              ),
              items: [
                const DropdownMenuItem(value: null, child: Text('Any stream')),
                for (final stream in widget.streams)
                  DropdownMenuItem(
                    value: stream['id'] as String,
                    child: Text((stream['name'] ?? stream['code']).toString()),
                  ),
              ],
              onChanged: (value) => setState(() => _stream = value),
            ),
          ],
          if (widget.departments.isNotEmpty) ...[
            const SizedBox(height: 8),
            DropdownButtonFormField<String?>(
              initialValue: _department,
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Department'),
              items: [
                const DropdownMenuItem(
                  value: null,
                  child: Text('No department'),
                ),
                for (final department in widget.departments)
                  DropdownMenuItem(
                    value: department['id'] as String,
                    child: Text(
                      (department['name'] ?? department['code']).toString(),
                    ),
                  ),
              ],
              onChanged: (value) => setState(() => _department = value),
            ),
          ],
          ListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Prerequisites'),
            subtitle: Text(_prerequisiteCaption),
            trailing: const Icon(Icons.edit_outlined),
            // Disabled until the catalogue is there to choose from.
            onTap: _catalogue.isEmpty ? null : _pickPrerequisites,
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Currently taught'),
            value: _active,
            onChanged: (value) => setState(() => _active = value),
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

/// Which courses must be passed first. Ticked out of the catalogue the form
/// already holds, so opening it costs nothing.
///
/// Whether the choice makes sense — a 300-level course requiring a 400-level
/// one, or a chain that closes on itself — is the server's to judge. This
/// picker only refuses the one case it can see on its own, and that exclusion
/// happens where the catalogue is built.
class _PrerequisiteDialog extends StatefulWidget {
  const _PrerequisiteDialog({required this.catalogue, required this.selected});

  final List<Map<String, dynamic>> catalogue;
  final List<String> selected;

  @override
  State<_PrerequisiteDialog> createState() => _PrerequisiteDialogState();
}

class _PrerequisiteDialogState extends State<_PrerequisiteDialog> {
  late final Set<String> _selected = {...widget.selected};
  String _filter = '';

  /// In memory: the catalogue is already loaded and one page long, so a round
  /// trip per keystroke would buy nothing.
  List<Map<String, dynamic>> get _visible {
    if (_filter.isEmpty) return widget.catalogue;
    final needle = _filter.toLowerCase();
    return [
      for (final subject in widget.catalogue)
        if ('${subject['code']} ${subject['title']}'.toLowerCase().contains(
          needle,
        ))
          subject,
    ];
  }

  @override
  Widget build(BuildContext context) {
    final visible = _visible;
    return AlertDialog(
      title: const Text('Prerequisites'),
      content: SizedBox(
        width: 400,
        height: 420,
        child: Column(
          children: [
            TextField(
              decoration: const InputDecoration(
                hintText: 'Code or title',
                prefixIcon: Icon(Icons.search),
                isDense: true,
                border: OutlineInputBorder(),
              ),
              onChanged: (value) => setState(() => _filter = value.trim()),
            ),
            const SizedBox(height: 8),
            Expanded(
              child: visible.isEmpty
                  ? const Center(child: Text('Nothing matches.'))
                  : ListView.builder(
                      itemCount: visible.length,
                      itemBuilder: (context, index) {
                        final subject = visible[index];
                        final id = subject['id'] as String;
                        final retired = subject['is_active'] == false;
                        return CheckboxListTile(
                          dense: true,
                          value: _selected.contains(id),
                          title: Text(
                            '${subject['code']} — ${subject['title']}',
                          ),
                          subtitle: retired ? const Text('Retired') : null,
                          onChanged: (on) => setState(
                            () => on == true
                                ? _selected.add(id)
                                : _selected.remove(id),
                          ),
                        );
                      },
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
        FilledButton(
          // The whole set, which is what the serializer replaces the relation
          // with — including the empty one, which is how the last is removed.
          onPressed: () => Navigator.pop(context, _selected.toList()),
          child: const Text('Done'),
        ),
      ],
    );
  }
}

import 'package:flutter/material.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';

/// Who sits in which class.
///
/// The screen exists because of what a promotion run leaves behind: the server
/// moves everyone up a year deliberately unclassed — guessing an arm would put
/// a pupil in a stream they never chose — so on the first morning of a session
/// the whole school is on the worklist below and somebody has to seat it.
///
/// One screen, not two, because seating an unplaced pupil and moving a placed
/// one are the same operation: both are a batch of one or many into a chosen
/// class, and both are refused by the same rules.
class ClassesScreen extends StatefulWidget {
  const ClassesScreen({super.key, required this.session});

  final Session session;

  @override
  State<ClassesScreen> createState() => _ClassesScreenState();
}

/// The unplaced students a class can actually take.
///
/// A class belongs to one level, and the server refuses anything else with
/// `ARM_MISMATCH`. Filtering here means the office never selects a JSS1 pupil
/// for an SS2 class and reads the refusal afterwards — the tick simply is not
/// offered. Pure, so the rule can be tested without a widget.
List<Map<String, dynamic>> eligibleFor(
  List<Map<String, dynamic>> unplaced,
  Map<String, dynamic>? arm,
) {
  if (arm == null) return const [];
  final level = arm['level'];
  return [
    for (final row in unplaced)
      if (row['level'] == level) row,
  ];
}

/// "SS2 Gold · 12 of 40". Occupancy is null on a build that has not been
/// through the annotated list; the label then says only what it knows.
String armCaption(Map<String, dynamic> arm) {
  final label = (arm['label'] ?? arm['name'] ?? '').toString();
  final enrolled = arm['enrolled'];
  final capacity = arm['capacity'];
  final retired = arm['is_active'] == false ? ' · retired' : '';
  if (enrolled == null) return '$label$retired';
  return '$label · $enrolled of $capacity$retired';
}

/// The write body for a class, built from what the form holds.
///
/// `capacity` empty is 40 rather than 0, which is the model's default and the
/// size of an ordinary Nigerian classroom — a blank box means "the usual", and
/// a class of zero seats would refuse every placement made into it.
///
/// `stream` and `form_teacher` null are both ordinary: a junior class has no
/// stream, and a class whose form teacher has not been decided yet still has
/// to exist so the year group can be seated.
Map<String, dynamic> armPayload({
  required String level,
  required String name,
  String? stream,
  String? formTeacher,
  String capacity = '',
  bool isActive = true,
}) => {
  'level': level,
  // "gold" and "Gold" under one level are two rows the unique constraint
  // allows and nobody reading a register can tell apart.
  'name': name.trim(),
  'stream': stream,
  'form_teacher': formTeacher,
  'capacity': int.tryParse(capacity.trim()) ?? 40,
  'is_active': isActive,
};

/// The names in a "Gold, Silver, Bronze" box, one class each.
///
/// A school opening a year group opens all of its arms in one sitting, and
/// keying the same form six times over is how the sixth one is forgotten until
/// a pupil cannot be seated in it.
///
/// Commas only, not spaces: "Gold Star" is one class with a two-word name, and
/// splitting it would silently make two.
///
/// Deduped case-insensitively, because the unique constraint is on the exact
/// name — "gold" and "Gold" are two rows the database allows and nobody
/// reading a register can tell apart.
List<String> armNames(String input) {
  final seen = <String>{};
  return [
    for (final part in input.split(','))
      if (part.trim().isNotEmpty && seen.add(part.trim().toLowerCase()))
        part.trim(),
  ];
}

/// What the class form pops with when the class it was editing is gone.
/// A sentinel rather than a second return channel: the dialog answers with an
/// id, and this is the id of nothing.
const _deletedArm = '';

/// Both halves of the screen in one load, so a placement refreshes the roster
/// and the worklist together — they are two views of one move.
class ClassBoard {
  const ClassBoard({required this.roster, required this.unplaced});

  final List<Map<String, dynamic>> roster;
  final List<Map<String, dynamic>> unplaced;
}

class _ClassesScreenState extends State<ClassesScreen> {
  final _key = GlobalKey<AsyncViewState<ClassBoard>>();
  late final Repository _repo = Repository(widget.session.api);

  List<Map<String, dynamic>> _arms = const [];
  List<Map<String, dynamic>> _levels = const [];
  List<Map<String, dynamic>> _streams = const [];
  List<Map<String, dynamic>> _staff = const [];
  String? _armId;
  final _selected = <String>{};
  bool _busy = false;

  Map<String, dynamic>? get _arm => _arms
      .cast<Map<String, dynamic>?>()
      .firstWhere((arm) => arm!['id'] == _armId, orElse: () => null);

  bool get _canManage => widget.session.can('academics.manage_enrolment');

  /// Seating a pupil and creating the class they sit in are different powers:
  /// the office does the first every morning, the second once a year.
  bool get _canEdit => widget.session.can('academics.manage_structure');

  @override
  void initState() {
    super.initState();
    _loadArms();
    if (_canEdit) _loadPickers();
  }

  /// The classes are the picker, so a failure here is not a failure of the
  /// screen: the worklist still loads and says who is waiting.
  ///
  /// Retired classes are listed only to whoever can edit one — otherwise a
  /// class the school stopped running could never be brought back, and there
  /// is no other way in to it. Nobody is seated into one: the worklist is
  /// hidden while a retired class is selected.
  Future<void> _loadArms() async {
    try {
      final arms = await _repo.classArms();
      if (!mounted) return;
      setState(() {
        _arms = [
          for (final arm in arms)
            if (_canEdit || arm['is_active'] != false) arm,
        ];
        _armId ??= _arms.isEmpty ? null : _arms.first['id'] as String?;
      });
    } on ApiError {
      // Leave the picker empty.
    } finally {
      // In the `finally` because a failed re-read of the classes must not stop
      // the roster and the worklist from reloading.
      _key.currentState?.reload();
    }
  }

  /// What the class form picks from. Only the levels are required — a school
  /// with no streams and no staff on file can still name its classes — so a
  /// failure here leaves the form usable rather than closing it.
  Future<void> _loadPickers() async {
    try {
      final lists = await Future.wait([
        _repo.classLevels(),
        _repo.streams(),
        _repo.staff(status: 'ACTIVE'),
      ]);
      if (!mounted) return;
      setState(() {
        _levels = lists[0];
        _streams = lists[1];
        _staff = lists[2];
      });
    } on ApiError {
      // Leave the pickers empty.
    }
  }

  /// `arm` null adds, otherwise it edits — the same fields against the same
  /// serializer, and a second widget would only change the verb on the button.
  Future<void> _editArm([Map<String, dynamic>? arm]) async {
    final result = await showDialog<String>(
      context: context,
      builder: (context) => _ArmDialog(
        repo: _repo,
        levels: _levels,
        streams: _streams,
        staff: _staff,
        arm: arm,
        classLabel: widget.session.label('CLASS'),
        groupLabel: widget.session.label('CLASS_GROUP'),
      ),
    );
    if (result == null) return;
    setState(() {
      // A deleted class leaves nothing to show a roster for, so the picker
      // falls back to whatever `_loadArms` finds first.
      _armId = result == _deletedArm ? null : result;
      _selected.clear();
    });
    _loadArms();
  }

  Future<ClassBoard> _load() async {
    final armId = _armId;
    final results = await Future.wait([
      armId == null
          ? Future.value(const <Map<String, dynamic>>[])
          : _repo.classRoster(armId),
      _repo.unplacedEnrolments(),
    ]);
    return ClassBoard(roster: results[0], unplaced: results[1]);
  }

  /// Seat a batch, then re-read both halves. The reload sits in the `finally`
  /// so a refusal still shows the state the server is actually in.
  Future<void> _place(
    List<String> enrolments, {
    bool enforceCapacity = true,
  }) async {
    final armId = _armId;
    if (armId == null || enrolments.isEmpty) return;
    setState(() => _busy = true);
    String? message;
    ApiError? refusal;
    try {
      final seated = await _repo.allocateToArm(
        armId,
        enrolments: enrolments,
        enforceCapacity: enforceCapacity,
      );
      _selected.removeAll(enrolments);
      final count = seated.isEmpty ? enrolments.length : seated.length;
      final who = 'student${count == 1 ? '' : 's'}';
      final where = _arm == null ? 'the class' : armCaption(_arm!);
      // An empty answer means the placement is in the outbox: the server has
      // not counted the seats yet, so there is nothing to report as placed.
      message = seated.isEmpty
          ? '$count $who queued for $where. They will be placed when there is '
                'a connection.'
          : '$count $who placed in $where.';
    } on ApiError catch (error) {
      refusal = error;
    } finally {
      if (mounted) setState(() => _busy = false);
      // Re-read the classes too, not only the board: seating somebody changes
      // the occupancy the picker is captioned with, so reloading one without
      // the other leaves "3 of 40" over a roster of four.
      _loadArms();
    }
    if (!mounted) return;

    // Asked after the spinner stops, not during it: a progress bar running
    // over a question says the app is working when it is waiting on a person.
    //
    // Capacity is the one refusal here a person can legitimately overrule — a
    // school that runs 45 to a room of 40 should record the truth rather than
    // edit the capacity to fit it. Everything else the server refuses (wrong
    // level, wrong stream, a leaver) is a mistake, not a policy.
    if (refusal != null) {
      if (refusal.code == 'ARM_FULL' && enforceCapacity) {
        if (await _confirmOverfill(refusal) == true) {
          return _place(enrolments, enforceCapacity: false);
        }
        if (!mounted) return;
      }
      message = refusal.message;
    }
    if (message != null) showApiMessage(context, message);
  }

  Future<bool?> _confirmOverfill(ApiError error) => showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('That class is full'),
      content: Text(
        '${error.message}\n\n'
        'Seat them anyway only if the class really does take more than its '
        'capacity says. The register, the mark sheet and the timetable all '
        'follow this class.',
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context, false),
          child: const Text('Leave it'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, true),
          child: const Text('Seat them anyway'),
        ),
      ],
    ),
  );

  /// Move a pupil already in a class. The destination list is every other
  /// class at their level — the rest would be refused, so they are not shown.
  Future<void> _move(Map<String, dynamic> row) async {
    final destinations = [
      for (final arm in _arms)
        if (arm['id'] != _armId &&
            arm['level'] == row['level'] &&
            arm['is_active'] != false)
          arm,
    ];
    if (destinations.isEmpty) {
      showApiMessage(
        context,
        'There is no other ${widget.session.label('CLASS_GROUP').toLowerCase()} '
        'at this ${widget.session.label('CLASS').toLowerCase()}.',
      );
      return;
    }
    final target = await showDialog<String>(
      context: context,
      builder: (context) => SimpleDialog(
        title: Text('Move ${row['student_name'] ?? 'this student'} to'),
        children: [
          for (final arm in destinations)
            SimpleDialogOption(
              onPressed: () => Navigator.pop(context, arm['id'] as String),
              child: Text(armCaption(arm)),
            ),
        ],
      ),
    );
    if (target == null) return;
    // Seated by the destination class, not this one: a move is a placement
    // read from the other end.
    final was = _armId;
    setState(() => _armId = target);
    await _place([row['id'] as String]);
    if (mounted) {
      setState(() => _armId = was);
      _key.currentState?.reload();
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final arm = _arm;
    return Scaffold(
      appBar: AppBar(title: Text('${widget.session.label('CLASS_GROUP')}s')),
      floatingActionButton: _canEdit
          ? FloatingActionButton.extended(
              onPressed: _busy ? null : () => _editArm(),
              icon: const Icon(Icons.add),
              label: Text('Add ${widget.session.label('CLASS_GROUP')}'),
            )
          : null,
      body: Column(
        children: [
          if (_busy) const LinearProgressIndicator(),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Row(
              children: [
                Expanded(
                  child: DropdownButtonFormField<String?>(
                    initialValue: _armId,
                    isExpanded: true,
                    decoration: InputDecoration(
                      labelText: widget.session.label('CLASS_GROUP'),
                    ),
                    items: [
                      for (final option in _arms)
                        DropdownMenuItem(
                          value: option['id'] as String,
                          child: Text(armCaption(option)),
                        ),
                    ],
                    onChanged: _busy
                        ? null
                        : (value) {
                            setState(() {
                              _armId = value;
                              // The worklist is filtered by the class's level,
                              // so a tick made against the old one may no
                              // longer be offered — and would be refused if it
                              // were sent.
                              _selected.clear();
                            });
                            _key.currentState?.reload();
                          },
                  ),
                ),
                if (_canEdit)
                  IconButton(
                    tooltip: 'Edit this ${widget.session.label('CLASS_GROUP')}',
                    icon: const Icon(Icons.edit_outlined),
                    onPressed: _busy || arm == null
                        ? null
                        : () => _editArm(arm),
                  ),
              ],
            ),
          ),
          Expanded(
            child: AsyncView<ClassBoard>(
              key: _key,
              load: _load,
              builder: (context, board) {
                final waiting = eligibleFor(board.unplaced, arm);
                return ListView(
                  padding: const EdgeInsets.only(bottom: 88),
                  children: [
                    // Nobody is seated into a retired class. Its roster still
                    // shows, because the pupils who were in it last session
                    // are the reason it is being looked at.
                    if (_canManage && arm != null && arm['is_active'] != false)
                      _Worklist(
                        waiting: waiting,
                        selected: _selected,
                        busy: _busy,
                        armLabel: armCaption(arm),
                        unplacedTotal: board.unplaced.length,
                        onToggle: (id, on) => setState(
                          () => on ? _selected.add(id) : _selected.remove(id),
                        ),
                        onPlace: () => _place(_selected.toList()),
                      ),
                    Padding(
                      padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
                      child: Text(
                        board.roster.isEmpty
                            ? 'Nobody is in this class yet.'
                            : '${board.roster.length} in this class',
                        style: text.titleSmall,
                      ),
                    ),
                    for (final row in board.roster)
                      ListTile(
                        leading: CircleAvatar(
                          child: Text('${row['roll_number'] ?? '–'}'),
                        ),
                        title: Text(
                          row['student_name'] as String? ?? 'Unnamed student',
                        ),
                        subtitle: Text(row['student_number'] as String? ?? ''),
                        trailing: _canManage
                            ? TextButton(
                                onPressed: _busy ? null : () => _move(row),
                                child: const Text('Move'),
                              )
                            : null,
                      ),
                  ],
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

/// Everyone still waiting for a class, ticked in and seated in one go.
class _Worklist extends StatelessWidget {
  const _Worklist({
    required this.waiting,
    required this.selected,
    required this.busy,
    required this.armLabel,
    required this.unplacedTotal,
    required this.onToggle,
    required this.onPlace,
  });

  final List<Map<String, dynamic>> waiting;
  final Set<String> selected;
  final bool busy;
  final String armLabel;

  /// Everyone unplaced, not only those this class could take — otherwise a
  /// school with nobody at this level reads "all seated" while a year group
  /// waits one class along.
  final int unplacedTotal;

  final void Function(String id, bool on) onToggle;
  final VoidCallback onPlace;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    if (unplacedTotal == 0) {
      return const SizedBox.shrink();
    }
    final ticked = waiting.where((row) => selected.contains(row['id'])).length;
    return Card(
      margin: const EdgeInsets.fromLTRB(12, 0, 12, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          ListTile(
            leading: const Icon(Icons.group_add_outlined),
            title: Text('$unplacedTotal waiting for a class'),
            subtitle: Text(
              waiting.isEmpty
                  ? 'None of them are at this class\'s level.'
                  : '${waiting.length} of them could join $armLabel.',
            ),
          ),
          for (final row in waiting)
            CheckboxListTile(
              dense: true,
              value: selected.contains(row['id']),
              onChanged: busy
                  ? null
                  : (on) => onToggle(row['id'] as String, on ?? false),
              title: Text(row['student_name'] as String? ?? 'Unnamed student'),
              subtitle: Text(
                [
                  row['student_number'] as String? ?? '',
                  row['level_code'] as String? ?? '',
                ].where((part) => part.isNotEmpty).join(' · '),
              ),
            ),
          if (waiting.isNotEmpty)
            Padding(
              padding: const EdgeInsets.all(12),
              child: FilledButton.icon(
                onPressed: busy || ticked == 0 ? null : onPlace,
                icon: const Icon(Icons.person_add_alt),
                label: Text(
                  ticked == 0
                      ? 'Tick who joins $armLabel'
                      : 'Place $ticked in $armLabel',
                ),
              ),
            ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
            child: Text(
              'A promoted year group arrives here with no class. Placing a '
              'student also gives them the next roll number in the register.',
              style: text.bodySmall,
            ),
          ),
        ],
      ),
    );
  }
}

/// Add or edit one class. Pops the id it wrote, [_deletedArm] if it deleted,
/// or null if nothing changed.
///
/// The classes had to be built in Django admin until now, which is a login
/// nobody in a school office has — and the year a school opens an extra arm
/// is the year it most needs one before the registrar can seat anybody.
class _ArmDialog extends StatefulWidget {
  const _ArmDialog({
    required this.repo,
    required this.levels,
    required this.streams,
    required this.staff,
    required this.classLabel,
    required this.groupLabel,
    this.arm,
  });

  final Repository repo;
  final List<Map<String, dynamic>> levels;
  final List<Map<String, dynamic>> streams;
  final List<Map<String, dynamic>> staff;
  final String classLabel;
  final String groupLabel;
  final Map<String, dynamic>? arm;

  @override
  State<_ArmDialog> createState() => _ArmDialogState();
}

class _ArmDialogState extends State<_ArmDialog> {
  late final _name = TextEditingController(
    text: widget.arm?['name'] as String? ?? '',
  );
  late final _capacity = TextEditingController(
    text: switch (widget.arm?['capacity']) {
      null => '',
      final capacity => '$capacity',
    },
  );
  late String? _level = widget.arm?['level'] as String?;
  late String? _stream = widget.arm?['stream'] as String?;
  late String? _formTeacher = widget.arm?['form_teacher'] as String?;
  late bool _active = widget.arm?['is_active'] != false;
  bool _saving = false;

  bool get _isNew => widget.arm == null;

  @override
  void dispose() {
    _name.dispose();
    _capacity.dispose();
    super.dispose();
  }

  /// The staff picker lists who is on duty, but the form teacher already on a
  /// class may be on leave or suspended. Their id still has to be a value the
  /// dropdown holds, or opening the form would silently reassign the class.
  List<DropdownMenuItem<String?>> get _teacherItems {
    final teacher = _formTeacher;
    return [
      const DropdownMenuItem(value: null, child: Text('Not decided yet')),
      for (final member in widget.staff)
        DropdownMenuItem(
          value: member['id'] as String,
          child: Text(
            (member['full_name'] ?? member['staff_number'] ?? '').toString(),
          ),
        ),
      if (teacher != null &&
          !widget.staff.any((member) => member['id'] == teacher))
        DropdownMenuItem(
          value: teacher,
          child: const Text('Current form teacher (not on duty)'),
        ),
    ];
  }

  Map<String, dynamic> _payload(String name) => armPayload(
    level: _level!,
    name: name,
    stream: _stream,
    formTeacher: _formTeacher,
    capacity: _capacity.text,
    isActive: _active,
  );

  /// One name edits, several add. The names share every other field on the
  /// form, which is what makes the batch worth having: a year group's arms
  /// differ by their name and nothing else.
  ///
  /// Not one request: there is no bulk endpoint, and writing one would put the
  /// all-or-nothing question on the server, where the answer is worse. A
  /// school that keys "Gold, Silver" with Silver already on file wants Gold
  /// created and Silver reported, not both refused.
  Future<void> _save() async {
    final level = _level;
    final names = _isNew ? armNames(_name.text) : [_name.text.trim()];
    if (level == null || names.isEmpty || names.first.isEmpty) {
      showApiMessage(
        context,
        'A ${widget.classLabel.toLowerCase()} and a name are required.',
      );
      return;
    }

    setState(() => _saving = true);
    String? firstId;
    final refused = <String>[];
    String? refusal;
    try {
      if (!_isNew) {
        final saved = await widget.repo.updateClassArm(
          widget.arm!['id'] as String,
          _payload(names.first),
        );
        firstId = saved['id'] as String;
      } else {
        for (final name in names) {
          try {
            final saved = await widget.repo.createClassArm(_payload(name));
            firstId ??= saved['id'] as String;
          } on ApiError catch (e) {
            // Two arms of one level sharing a name is the common refusal, and
            // the server's message names it.
            refused.add(name);
            refusal = e.message;
          }
        }
      }
    } on ApiError catch (e) {
      refusal = e.message;
    } finally {
      if (mounted) setState(() => _saving = false);
    }
    if (!mounted) return;

    // Nothing was written, so the form stays open on what was keyed.
    if (firstId == null) {
      showApiMessage(context, refusal ?? 'Nothing was saved.');
      return;
    }
    final made = names.length - refused.length;
    if (refused.isNotEmpty) {
      showApiMessage(
        context,
        '$made added. ${refused.join(', ')} not: ${refusal ?? ''}'.trim(),
      );
    } else if (made > 1) {
      showApiMessage(
        context,
        '$made ${widget.groupLabel.toLowerCase()}s added.',
      );
    }
    Navigator.pop(context, firstId);
  }

  Future<void> _delete() async {
    final group = widget.groupLabel.toLowerCase();
    final deleted = await confirmDelete(
      context,
      title: 'Delete this $group?',
      message:
          '${_name.text} is removed from the school.\n\n'
          'If anybody has ever sat in it, untick "In use" instead — that keeps '
          'the register, the marks and the timetable recorded against it, and '
          'takes it out of every picker.',
      onConfirm: () => widget.repo.deleteClassArm(widget.arm!['id'] as String),
    );
    if (deleted && mounted) Navigator.pop(context, _deletedArm);
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(
      '${_isNew ? 'Add' : 'Edit'} ${widget.groupLabel.toLowerCase()}',
    ),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          DropdownButtonFormField<String?>(
            initialValue: _level,
            isExpanded: true,
            decoration: InputDecoration(labelText: widget.classLabel),
            items: [
              for (final level in widget.levels)
                DropdownMenuItem(
                  value: level['id'] as String,
                  child: Text((level['name'] ?? level['code']).toString()),
                ),
            ],
            onChanged: (value) => setState(() => _level = value),
          ),
          TextField(
            controller: _name,
            decoration: InputDecoration(
              labelText: _isNew ? 'Name, or names' : 'Name',
              helperText: _isNew
                  ? 'A, B, Gold — one ${widget.groupLabel.toLowerCase()} each'
                  : 'A, B, Gold, Silver',
            ),
            textInputAction: TextInputAction.next,
          ),
          TextField(
            controller: _capacity,
            decoration: const InputDecoration(
              labelText: 'Capacity',
              helperText: 'Leave blank for 40',
            ),
            keyboardType: TextInputType.number,
          ),
          // Hidden when the school streams nobody: an empty picker is a
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
                const DropdownMenuItem(value: null, child: Text('No stream')),
                for (final stream in widget.streams)
                  DropdownMenuItem(
                    value: stream['id'] as String,
                    child: Text((stream['name'] ?? stream['code']).toString()),
                  ),
              ],
              onChanged: (value) => setState(() => _stream = value),
            ),
          ],
          const SizedBox(height: 8),
          DropdownButtonFormField<String?>(
            initialValue: _formTeacher,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Form teacher'),
            items: _teacherItems,
            onChanged: (value) => setState(() => _formTeacher = value),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('In use'),
            subtitle: const Text('A retired class takes no more students'),
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

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';
import '../phone.dart';

const _statuses = {
  'ACTIVE': 'Active',
  'ADMITTED': 'Admitted',
  'APPLICANT': 'Applicant',
  'GRADUATED': 'Graduated',
  'WITHDRAWN': 'Withdrawn',
  'SUSPENDED': 'Suspended',
  'TRANSFERRED': 'Transferred',
};

const _relationships = {
  'MOTHER': 'Mother',
  'FATHER': 'Father',
  'GUARDIAN': 'Guardian',
  'SIBLING': 'Sibling',
  'OTHER': 'Other',
};

/// The student register: search it, open a record, correct it, add one.
///
/// A term's intake arrives through the import screen — this is the record the
/// file could not cover: the child who turned up in week three, and the name
/// somebody spelled wrong on the way in. Anyone signed in may read the list
/// (their own wards, their own classes — the server scopes it); only
/// `people.manage_student` may write.
class StudentsScreen extends StatefulWidget {
  const StudentsScreen({super.key, required this.session});

  final Session session;

  @override
  State<StudentsScreen> createState() => _StudentsScreenState();
}

class _StudentsScreenState extends State<StudentsScreen> {
  final _key = GlobalKey<AsyncViewState<List<Map<String, dynamic>>>>();
  late final Repository _repo = Repository(widget.session.api);
  String _search = '';
  String? _status = 'ACTIVE';

  /// Which record the right-hand pane is showing, and whether it is showing
  /// one at all. Null id with the pane open is the blank "add" form. Both are
  /// dead weight on a phone, where the same form arrives as a sheet.
  String? _selectedId;
  bool _paneOpen = false;

  void _reload() => _key.currentState?.reload();

  /// Same form either way; only where it is put changes. On a wide screen it
  /// takes the second column, on a phone it comes up as a sheet.
  Future<void> _edit({String? studentId, required bool wide}) async {
    if (wide) {
      setState(() {
        _selectedId = studentId;
        _paneOpen = true;
      });
      return;
    }
    final changed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      // No width override: Material 3 already caps the sheet at 640 and
      // centres it, which is what a form wants on a tablet or a browser.
      builder: (context) => _StudentForm(
        repo: _repo,
        studentId: studentId,
        canManage: widget.session.can('people.manage_student'),
      ),
    );
    if (changed == true) _reload();
  }

  /// Keyed on the id so picking another pupil rebuilds the form from scratch
  /// rather than leaving the last one's text in the fields.
  Widget _pane() => _paneOpen
      ? _StudentForm(
          key: ValueKey(_selectedId),
          repo: _repo,
          studentId: _selectedId,
          canManage: widget.session.can('people.manage_student'),
          onClose: (changed) {
            if (changed) _reload();
            setState(() => _paneOpen = false);
          },
        )
      : const EmptyState(
          icon: Icons.badge_outlined,
          message: 'Pick a student to open their record.',
        );

  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, box) {
      final wide = box.maxWidth >= kTwoColumnFrom;
      return Scaffold(
        appBar: AppBar(title: const Text('Students')),
        floatingActionButton: widget.session.can('people.manage_student')
            ? FloatingActionButton.extended(
                onPressed: () => _edit(wide: wide),
                icon: const Icon(Icons.person_add_alt),
                label: const Text('Add student'),
              )
            : null,
        body: wide
            ? Row(
                children: [
                  SizedBox(width: kMasterWidth, child: _list(wide: true)),
                  const VerticalDivider(width: 1),
                  Expanded(child: _pane()),
                ],
              )
            : _list(wide: false),
      );
    },
  );

  Widget _list({required bool wide}) => Column(
    children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
        child: TextField(
          decoration: const InputDecoration(
            hintText: 'Name or admission number',
            prefixIcon: Icon(Icons.search),
            isDense: true,
            border: OutlineInputBorder(),
          ),
          textInputAction: TextInputAction.search,
          // On submit, not per keystroke: a school has three thousand
          // students and this network does not spare eight round trips.
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
            for (final entry in {'': 'All', ..._statuses}.entries)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 4),
                child: ChoiceChip(
                  label: Text(entry.value),
                  selected: (_status ?? '') == entry.key,
                  onSelected: (_) {
                    setState(
                      () => _status = entry.key.isEmpty ? null : entry.key,
                    );
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
          load: () => _repo.students(
            status: _status,
            search: _search.isEmpty ? null : _search,
          ),
          empty: const EmptyState(
            icon: Icons.school_outlined,
            message: 'No students match this filter.',
          ),
          builder: (context, students) => ListView.separated(
            itemCount: students.length,
            separatorBuilder: (_, _) => const Divider(height: 1),
            itemBuilder: (context, index) {
              final row = students[index];
              return ListTile(
                leading: CircleAvatar(
                  child: Text(_initials(row['full_name'] as String? ?? '')),
                ),
                title: Text(row['full_name'] as String? ?? ''),
                subtitle: Text(
                  [
                    row['display_number'] ?? row['admission_number'] ?? '',
                    _statuses[row['status']] ?? row['status'] ?? '',
                  ].where((p) => '$p'.isNotEmpty).join(' · '),
                ),
                selected: wide && row['id'] == _selectedId,
                onTap: () => _edit(studentId: row['id'] as String, wide: wide),
              );
            },
          ),
        ),
      ),
    ],
  );
}

String _initials(String name) {
  final parts = name.split(' ').where((p) => p.isNotEmpty).toList();
  if (parts.isEmpty) return '?';
  return parts.length == 1
      ? parts.first[0].toUpperCase()
      : '${parts.first[0]}${parts.last[0]}'.toUpperCase();
}

/// The write body for a student, built from what the form holds.
///
/// `current_level` is never asked for: an arm already belongs to a level, and
/// a record whose two disagree puts a child on the wrong broadsheet. The
/// foreign keys clear with null and the blankable text with `''`, which is
/// what each field on the serializer accepts.
Map<String, dynamic> studentPayload({
  required String firstName,
  required String lastName,
  String otherName = '',
  String gender = '',
  DateTime? dateOfBirth,
  String phone = '',
  String? arm,
  List<Map<String, dynamic>> arms = const [],
  String status = 'ACTIVE',
}) {
  String? level;
  if (arm != null) {
    for (final candidate in arms) {
      if (candidate['id'] == arm) level = candidate['level'] as String?;
    }
  }
  return {
    'first_name': firstName.trim(),
    'last_name': lastName.trim(),
    'other_name': otherName.trim(),
    'gender': gender,
    // Date only, local fields: a DOB shifted by a timezone is a birthday on
    // the wrong day for every child born after 1am.
    'date_of_birth': dateOfBirth?.toIso8601String().split('T').first,
    'phone': phone,
    'current_arm': arm,
    'current_level': level,
    'status': status,
  };
}

class _StudentForm extends StatefulWidget {
  const _StudentForm({
    super.key,
    required this.repo,
    required this.canManage,
    this.studentId,
    this.onClose,
  });

  final Repository repo;
  final bool canManage;

  /// Null adds a new record; anything else edits that one.
  final String? studentId;

  /// How the form gets rid of itself. Null means it is a route — a sheet —
  /// and pops. Supplied means it is a pane, where popping would take the
  /// whole screen with it. The bool is whether the list needs re-reading.
  final ValueChanged<bool>? onClose;

  @override
  State<_StudentForm> createState() => _StudentFormState();
}

class _StudentFormState extends State<_StudentForm> {
  final _firstName = TextEditingController();
  final _lastName = TextEditingController();
  final _otherName = TextEditingController();
  final _phone = TextEditingController();

  List<Map<String, dynamic>> _arms = const [];
  List<Map<String, dynamic>> _guardians = const [];
  List<Map<String, dynamic>> _documents = const [];
  String _gender = '';
  String _status = 'ACTIVE';
  String? _arm;
  String? _photo;
  DateTime? _dob;
  String _title = 'Add student';
  bool _loading = true;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _firstName.dispose();
    _lastName.dispose();
    _otherName.dispose();
    _phone.dispose();
    super.dispose();
  }

  void _close({required bool changed}) {
    final onClose = widget.onClose;
    if (onClose == null) {
      Navigator.pop(context, changed);
    } else {
      onClose(changed);
    }
  }

  Future<void> _load() async {
    try {
      final arms = await widget.repo.classArms();
      final student = widget.studentId == null
          ? null
          : await widget.repo.student(widget.studentId!);
      // Documents are their own endpoint, and only for a record that exists.
      final documents = widget.studentId == null || !widget.canManage
          ? const <Map<String, dynamic>>[]
          : await widget.repo.studentDocuments(widget.studentId!);
      if (!mounted) return;
      setState(() {
        _arms = arms;
        _documents = documents;
        _loading = false;
        if (student == null) return;
        _firstName.text = student['first_name'] as String? ?? '';
        _lastName.text = student['last_name'] as String? ?? '';
        _otherName.text = student['other_name'] as String? ?? '';
        _phone.text = student['phone'] as String? ?? '';
        _gender = student['gender'] as String? ?? '';
        _status = student['status'] as String? ?? 'ACTIVE';
        _arm = student['current_arm'] as String?;
        _dob = DateTime.tryParse(student['date_of_birth'] as String? ?? '');
        _photo = student['photo'] as String?;
        _guardians = Repository.rows(student['guardians']);
        _title = [
          student['full_name'],
          student['display_number'],
        ].where((p) => '$p'.isNotEmpty && p != null).join(' · ');
      });
    } on ApiError catch (e) {
      if (mounted) {
        showApiMessage(context, e.message);
        _close(changed: false);
      }
    }
  }

  Future<void> _pickDob() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: _dob ?? DateTime(now.year - 10),
      firstDate: DateTime(now.year - 80),
      lastDate: now,
    );
    if (picked != null) setState(() => _dob = picked);
  }

  Future<void> _addGuardian() async {
    final added = await showDialog<bool>(
      context: context,
      builder: (context) =>
          _GuardianDialog(repo: widget.repo, studentId: widget.studentId!),
    );
    if (added == true) await _reloadGuardians();
  }

  /// Re-read rather than patching the local list: the server may have matched
  /// an existing parent, and naming a new primary demotes whoever held it.
  Future<void> _reloadGuardians() async {
    final student = await widget.repo.student(widget.studentId!);
    if (mounted) {
      setState(() => _guardians = Repository.rows(student['guardians']));
    }
  }

  Future<void> _removeGuardian(Map<String, dynamic> link) async {
    final name =
        (link['guardian'] as Map?)?['full_name'] as String? ?? 'This guardian';
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Detach this guardian?'),
        content: Text(
          '$name stops seeing this child in their app — results, invoices and '
          'the fee reminders that go with them. Their own account and any '
          'other children stay as they are.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Detach'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    setState(() => _saving = true);
    try {
      await widget.repo.unlinkGuardian(widget.studentId!, link['id'] as String);
      await _reloadGuardians();
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _pickPhoto() async {
    // ponytail: the file picker, not a camera plugin — a passport photo in a
    // Nigerian school office is a scan or a JPEG someone was sent, and adding
    // image_picker buys a camera nobody asked for. Revisit if the office
    // starts photographing intake at the desk.
    final picked = await FilePicker.platform.pickFiles(
      type: FileType.image,
      withData: true,
    );
    if (picked == null || picked.files.isEmpty) return;
    final file = picked.files.first;
    final bytes = file.bytes;
    if (bytes == null) {
      if (mounted) {
        showApiMessage(
          context,
          'That image could not be read from the device.',
        );
      }
      return;
    }

    setState(() => _saving = true);
    try {
      final student = await widget.repo.uploadStudentPhoto(
        widget.studentId!,
        filename: file.name,
        bytes: bytes,
      );
      if (mounted) setState(() => _photo = student['photo'] as String?);
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  /// Title first, then the file: a folder of "IMG_2043.jpg" is a folder
  /// nobody can search, and asking afterwards means the upload is already
  /// gone if they cancel.
  Future<void> _addDocument() async {
    final title = await showDialog<String>(
      context: context,
      builder: (context) => const _DocumentTitleDialog(),
    );
    if (title == null || title.isEmpty) return;

    final picked = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: documentExtensions,
      withData: true,
    );
    if (picked == null || picked.files.isEmpty) return;
    final file = picked.files.first;
    final bytes = file.bytes;
    if (bytes == null) {
      if (mounted) {
        showApiMessage(context, 'That file could not be read from the device.');
      }
      return;
    }
    // The server refuses this too. Checking here saves the upload itself,
    // which on a metered phone connection is the part that costs.
    if (bytes.length > maxDocumentBytes) {
      if (mounted) {
        showApiMessage(
          context,
          'That file is ${(bytes.length / 1048576).toStringAsFixed(1)} MB; '
          'the limit is ${maxDocumentBytes ~/ 1048576} MB.',
        );
      }
      return;
    }

    setState(() => _saving = true);
    try {
      await widget.repo.uploadStudentDocument(
        studentId: widget.studentId!,
        title: title,
        filename: file.name,
        bytes: bytes,
      );
      await _reloadDocuments();
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _reloadDocuments() async {
    final documents = await widget.repo.studentDocuments(widget.studentId!);
    if (mounted) setState(() => _documents = documents);
  }

  Future<void> _removeDocument(Map<String, dynamic> document) async {
    final removed = await confirmDelete(
      context,
      title: 'Remove this document?',
      message:
          '${document['title']} is deleted for good — a document is a file, '
          'not a record, and there is nothing to restore it from.',
      confirmLabel: 'Remove',
      onConfirm: () =>
          widget.repo.deleteStudentDocument(document['id'] as String),
    );
    if (removed) await _reloadDocuments();
  }

  /// Soft on the server, and the wording says so: the pupil leaves the
  /// register and their results, attendance and invoices stay put. A leaver
  /// should be marked WITHDRAWN or TRANSFERRED instead — that closes the
  /// enrolment and stops next term's bill — so this is for the duplicate and
  /// the record entered against the wrong child.
  Future<void> _delete() async {
    final deleted = await confirmDelete(
      context,
      title: 'Remove this record?',
      message:
          '$_title comes off the register. Their results, attendance and '
          'invoices are kept.\n\n'
          'If the pupil has left the school, close the record instead by '
          'setting the status to Withdrawn or Transferred.',
      confirmLabel: 'Remove',
      onConfirm: () => widget.repo.deleteStudent(widget.studentId!),
    );
    if (deleted && mounted) _close(changed: true);
  }

  Future<void> _save() async {
    if (_firstName.text.trim().isEmpty || _lastName.text.trim().isEmpty) {
      showApiMessage(context, 'A first and last name are required.');
      return;
    }
    // Checked here so an obvious typo costs no round trip; the server still
    // has the last word, because only it knows the NCC ranges. A student may
    // legitimately carry no number of their own, so blank is allowed and only
    // a wrong one is refused.
    final typed = _phone.text.trim();
    if (typed.isNotEmpty) {
      final problem = phoneErrorFor(typed);
      if (problem != null) {
        showApiMessage(context, problem);
        return;
      }
    }

    final payload = studentPayload(
      firstName: _firstName.text,
      lastName: _lastName.text,
      otherName: _otherName.text,
      gender: _gender,
      dateOfBirth: _dob,
      phone: typed.isEmpty ? '' : normalizePhone(typed),
      arm: _arm,
      arms: _arms,
      status: _status,
    );

    setState(() => _saving = true);
    try {
      if (widget.studentId == null) {
        await widget.repo.createStudent(payload);
      } else {
        await widget.repo.updateStudent(widget.studentId!, payload);
      }
      if (mounted) _close(changed: true);
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const SizedBox(
        height: 200,
        child: Center(child: CircularProgressIndicator()),
      );
    }

    final text = Theme.of(context).textTheme;
    final enabled = widget.canManage && !_saving;

    return SafeArea(
      child: Padding(
        padding: EdgeInsets.fromLTRB(
          20,
          20,
          20,
          20 + MediaQuery.viewInsetsOf(context).bottom,
        ),
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  // A photo needs a record to hang off, so it appears once the
                  // student exists — the same reason guardians do.
                  if (widget.studentId != null) ...[
                    CircleAvatar(
                      radius: 28,
                      foregroundImage: (_photo ?? '').startsWith('http')
                          ? NetworkImage(_photo!)
                          : null,
                      child: Text(_initials(_title)),
                    ),
                    const SizedBox(width: 12),
                  ],
                  Expanded(child: Text(_title, style: text.titleLarge)),
                  if (widget.studentId != null && widget.canManage)
                    IconButton(
                      onPressed: _saving ? null : _pickPhoto,
                      icon: const Icon(Icons.add_a_photo_outlined),
                      tooltip: 'Change photo',
                    ),
                ],
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _firstName,
                enabled: enabled,
                decoration: const InputDecoration(labelText: 'First name'),
                textInputAction: TextInputAction.next,
              ),
              TextField(
                controller: _lastName,
                enabled: enabled,
                decoration: const InputDecoration(labelText: 'Last name'),
                textInputAction: TextInputAction.next,
              ),
              TextField(
                controller: _otherName,
                enabled: enabled,
                decoration: const InputDecoration(labelText: 'Other name'),
                textInputAction: TextInputAction.next,
              ),
              TextField(
                controller: _phone,
                enabled: enabled,
                decoration: const InputDecoration(
                  labelText: 'Phone',
                  helperText: 'Optional — a guardian keeps their own number',
                ),
                keyboardType: TextInputType.phone,
              ),
              const SizedBox(height: 16),

              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(value: 'M', label: Text('Male')),
                  ButtonSegment(value: 'F', label: Text('Female')),
                  ButtonSegment(value: '', label: Text('Unsaid')),
                ],
                selected: {_gender},
                onSelectionChanged: enabled
                    ? (values) => setState(() => _gender = values.first)
                    : null,
              ),
              const SizedBox(height: 8),

              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.cake_outlined),
                title: Text(
                  _dob == null
                      ? 'Date of birth'
                      : _dob!.toIso8601String().split('T').first,
                ),
                trailing: const Icon(Icons.edit_calendar_outlined),
                onTap: enabled ? _pickDob : null,
              ),

              DropdownButtonFormField<String?>(
                initialValue: _arm,
                isExpanded: true,
                decoration: const InputDecoration(labelText: 'Class'),
                items: [
                  const DropdownMenuItem(value: null, child: Text('No class')),
                  for (final arm in _arms)
                    DropdownMenuItem(
                      value: arm['id'] as String?,
                      // The serializer's "SS2 Gold": arm names repeat across
                      // levels, so the name alone picks the wrong class.
                      child: Text(
                        (arm['label'] ?? arm['name'] ?? '').toString(),
                      ),
                    ),
                ],
                onChanged: enabled
                    ? (value) => setState(() => _arm = value)
                    : null,
              ),
              const SizedBox(height: 16),

              Text('Status', style: text.labelLarge),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                children: [
                  for (final entry in _statuses.entries)
                    ChoiceChip(
                      label: Text(entry.value),
                      selected: _status == entry.key,
                      onSelected: enabled
                          ? (_) => setState(() => _status = entry.key)
                          : null,
                    ),
                ],
              ),
              const SizedBox(height: 16),

              // A new record has no id to hang a guardian off yet, so this
              // appears the second time the form is opened.
              if (widget.studentId != null) ...[
                Row(
                  children: [
                    Text('Guardians', style: text.labelLarge),
                    const Spacer(),
                    if (widget.canManage)
                      TextButton.icon(
                        onPressed: _saving ? null : _addGuardian,
                        icon: const Icon(Icons.person_add_alt_1_outlined),
                        label: const Text('Add'),
                      ),
                  ],
                ),
                if (_guardians.isEmpty)
                  Text(
                    'Nobody is attached — fee reminders and report cards have '
                    'no phone to go to.',
                    style: text.bodyMedium,
                  )
                else
                  for (final link in _guardians)
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      dense: true,
                      leading: Icon(
                        link['is_primary'] == true
                            ? Icons.star
                            : Icons.person_outline,
                      ),
                      title: Text(
                        (link['guardian'] as Map?)?['full_name'] as String? ??
                            '',
                      ),
                      subtitle: Text(
                        [
                          _relationships[link['relationship']] ??
                              link['relationship'] ??
                              '',
                          formatPhone(
                            (link['guardian'] as Map?)?['phone'] as String? ??
                                '',
                          ),
                        ].where((p) => '$p'.isNotEmpty).join(' · '),
                      ),
                      trailing: widget.canManage
                          ? IconButton(
                              onPressed: _saving
                                  ? null
                                  : () => _removeGuardian(link),
                              icon: const Icon(Icons.link_off),
                              tooltip: 'Detach',
                            )
                          : null,
                    ),
                const SizedBox(height: 12),
              ],

              // The intake file: birth certificate, testimonial, O-level
              // result. Office staff only — a guardian's app has no business
              // listing what the school holds on paper.
              if (widget.studentId != null && widget.canManage) ...[
                Row(
                  children: [
                    Text('Documents', style: text.labelLarge),
                    const Spacer(),
                    TextButton.icon(
                      onPressed: _saving ? null : _addDocument,
                      icon: const Icon(Icons.attach_file_outlined),
                      label: const Text('Upload'),
                    ),
                  ],
                ),
                if (_documents.isEmpty)
                  Text(
                    'Nothing on file. PDF or a photo, up to '
                    '${maxDocumentBytes ~/ 1048576} MB.',
                    style: text.bodyMedium,
                  )
                else
                  for (final document in _documents)
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      dense: true,
                      leading: const Icon(Icons.description_outlined),
                      title: Text(document['title'] as String? ?? ''),
                      subtitle: Text(
                        '${document['created_at'] ?? ''}'.split('T').first,
                      ),
                      trailing: IconButton(
                        onPressed: _saving
                            ? null
                            : () => _removeDocument(document),
                        icon: const Icon(Icons.delete_outline),
                        tooltip: 'Remove',
                      ),
                    ),
                const SizedBox(height: 12),
              ],

              // OverflowBar, not a Row: "Remove", "Close" and "Save changes"
              // are 120px wider than a 360dp sheet, and a Row simply clips the
              // save button off the edge. This stacks them instead.
              OverflowBar(
                alignment: MainAxisAlignment.end,
                overflowAlignment: OverflowBarAlignment.end,
                spacing: 8,
                children: [
                  if (widget.studentId != null && widget.canManage)
                    TextButton.icon(
                      onPressed: _saving ? null : _delete,
                      icon: const Icon(Icons.person_remove_outlined),
                      label: const Text('Remove'),
                    ),
                  TextButton(
                    onPressed: _saving ? null : () => _close(changed: false),
                    child: const Text('Close'),
                  ),
                  if (widget.canManage)
                    FilledButton(
                      onPressed: _saving ? null : _save,
                      child: Text(
                        widget.studentId == null ? 'Add' : 'Save changes',
                      ),
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// What the file is, asked before it is chosen. The common four are offered
/// as chips because they are what an intake folder actually contains.
class _DocumentTitleDialog extends StatefulWidget {
  const _DocumentTitleDialog();

  @override
  State<_DocumentTitleDialog> createState() => _DocumentTitleDialogState();
}

class _DocumentTitleDialogState extends State<_DocumentTitleDialog> {
  static const _common = [
    'Birth certificate',
    'Testimonial',
    'O-level result',
    'Transfer letter',
  ];

  final _title = TextEditingController();

  @override
  void dispose() {
    _title.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('What is this document?'),
    content: Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        TextField(
          controller: _title,
          autofocus: true,
          decoration: const InputDecoration(labelText: 'Title'),
          onChanged: (_) => setState(() {}),
          onSubmitted: (value) => Navigator.pop(
            context,
            value.trim().isEmpty ? null : value.trim(),
          ),
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 8,
          children: [
            for (final name in _common)
              ActionChip(
                label: Text(name),
                onPressed: () => setState(() => _title.text = name),
              ),
          ],
        ),
      ],
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: _title.text.trim().isEmpty
            ? null
            : () => Navigator.pop(context, _title.text.trim()),
        child: const Text('Choose file'),
      ),
    ],
  );
}

/// Attaching a parent, which is mostly a phone number: the server matches on
/// it, so this form is how a second child reaches the login their parent
/// already has. The name fields fill blanks on a matched record and never
/// overwrite what is already there.
class _GuardianDialog extends StatefulWidget {
  const _GuardianDialog({required this.repo, required this.studentId});

  final Repository repo;
  final String studentId;

  @override
  State<_GuardianDialog> createState() => _GuardianDialogState();
}

class _GuardianDialogState extends State<_GuardianDialog> {
  final _phone = TextEditingController();
  final _firstName = TextEditingController();
  final _lastName = TextEditingController();
  String _relationship = 'GUARDIAN';
  bool _primary = false;
  bool _saving = false;

  @override
  void dispose() {
    _phone.dispose();
    _firstName.dispose();
    _lastName.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final problem = phoneErrorFor(_phone.text.trim());
    if (problem != null) {
      showApiMessage(context, problem);
      return;
    }
    setState(() => _saving = true);
    try {
      await widget.repo.linkGuardian(
        widget.studentId,
        phone: normalizePhone(_phone.text.trim()),
        firstName: _firstName.text.trim(),
        lastName: _lastName.text.trim(),
        relationship: _relationship,
        isPrimary: _primary,
      );
      if (mounted) Navigator.pop(context, true);
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Add guardian'),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: _phone,
            decoration: const InputDecoration(labelText: 'Phone'),
            keyboardType: TextInputType.phone,
            textInputAction: TextInputAction.next,
          ),
          TextField(
            controller: _firstName,
            decoration: const InputDecoration(labelText: 'First name'),
            textInputAction: TextInputAction.next,
          ),
          TextField(
            controller: _lastName,
            decoration: const InputDecoration(labelText: 'Last name'),
          ),
          const SizedBox(height: 16),
          DropdownButtonFormField<String>(
            initialValue: _relationship,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Relationship'),
            items: [
              for (final entry in _relationships.entries)
                DropdownMenuItem(value: entry.key, child: Text(entry.value)),
            ],
            onChanged: _saving
                ? null
                : (value) =>
                      setState(() => _relationship = value ?? 'GUARDIAN'),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Primary contact'),
            subtitle: const Text('Gets the fee reminders and the report card'),
            value: _primary,
            onChanged: _saving
                ? null
                : (value) => setState(() => _primary = value),
          ),
          Text(
            'Matched on the phone number: a parent already in the school keeps '
            'one record and one login.',
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
      FilledButton(onPressed: _saving ? null : _save, child: const Text('Add')),
    ],
  );
}

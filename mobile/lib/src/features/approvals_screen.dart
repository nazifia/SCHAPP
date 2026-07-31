import 'package:flutter/material.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';

/// The course-registration queue: approve, or refuse with a reason.
///
/// Two steps in one list, because they are the same queue seen by two people:
/// an adviser turns SUBMITTED into ADVISER_APPROVED, the head of department
/// turns that into APPROVED, and only the second is final. The chip on each
/// row says which step it is waiting on, so nobody has to learn the order.
///
/// Refusing is the half that had no screen at all — a registration nobody
/// acted on stayed SUBMITTED, and SUBMITTED is scored on the report card.
///
/// The queue is grouped by student and scoped to one term, because that is how
/// the decision is actually made: an adviser signs off a student's semester,
/// not a course. A flat list across every term ever run made a signature into
/// eight taps and eight reloads, scattered among last year's abandoned rows.
class ApprovalsScreen extends StatefulWidget {
  const ApprovalsScreen({super.key, required this.session});

  final Session session;

  @override
  State<ApprovalsScreen> createState() => _ApprovalsScreenState();
}

/// The two open states, in the order they happen, and the settled one that is
/// still somebody's problem.
///
/// Refused is on the same screen as the queue because a refusal is the only
/// decision here nobody could take back: `rejection_reason` was written by the
/// server and read by nothing, so a course refused by mistake — or refused on
/// a reason the student then answered — was invisible from the moment it left
/// the queue.
const _queue = {
  'SUBMITTED': 'Waiting on adviser',
  'ADVISER_APPROVED': 'Waiting on HOD',
  'REJECTED': 'Refused',
};

/// Settled: nothing to approve, only a refusal to read or take back.
const _refused = 'REJECTED';

/// One student's rows in the queue, which is the unit a decision is made in.
class RegistrationGroup {
  RegistrationGroup(this.studentName, this.rows);

  final String studentName;
  final List<Map<String, dynamic>> rows;

  int get creditUnits => rows.fold(
    0,
    (sum, row) => sum + (int.tryParse('${row['credit_units'] ?? 0}') ?? 0),
  );
}

/// Collapse the flat queue into one entry per student, students A–Z.
///
/// Keyed on the enrolment rather than the name: two pupils called Chidi Okeke
/// are one row each, not one row with sixteen courses. The name is only what
/// gets printed. Pure so the grouping can be tested without a widget.
List<RegistrationGroup> groupByStudent(List<Map<String, dynamic>> rows) {
  final groups = <String, RegistrationGroup>{};
  for (final row in rows) {
    final name = row['student_name'] as String? ?? 'Unknown student';
    final key = row['enrolment'] as String? ?? name;
    (groups[key] ??= RegistrationGroup(name, [])).rows.add(row);
  }
  return groups.values.toList()
    ..sort((a, b) => a.studentName.compareTo(b.studentName));
}

class _ApprovalsScreenState extends State<ApprovalsScreen> {
  final _key = GlobalKey<AsyncViewState<List<Map<String, dynamic>>>>();
  late final Repository _repo = Repository(widget.session.api);
  String _status = 'SUBMITTED';
  bool _busy = false;

  List<Map<String, dynamic>> _terms = const [];

  /// Null is "every term", which is what the screen used to do always. It is
  /// still worth having: a registration abandoned in a closed term is exactly
  /// the thing an adviser is asked to go back and clear.
  String? _termId;

  @override
  void initState() {
    super.initState();
    _loadTerms();
  }

  /// Terms are for the picker only, so a failure here is not a failure of the
  /// screen: the queue still loads, unscoped, and the dropdown stays empty.
  Future<void> _loadTerms() async {
    try {
      final terms = await _repo.terms();
      if (!mounted) return;
      setState(() {
        _terms = terms;
        // The term the school is in, not the first one ever created.
        _termId =
            terms.firstWhere(
                  (term) => term['is_current'] == true,
                  orElse: () => const <String, dynamic>{},
                )['id']
                as String?;
      });
      // Only if the scope actually changed. Without the guard a school with no
      // current term fetches the same unscoped queue twice on every open, and
      // this one asks for 200 rows.
      if (_termId != null) _key.currentState?.reload();
    } on ApiError {
      // Leave the picker empty and the queue unscoped.
    }
  }

  /// Run a batch of writes, then re-read the queue once. The reload is in the
  /// `finally` so a half-applied batch still shows what did land.
  Future<void> _run(Future<String?> Function() body) async {
    setState(() => _busy = true);
    String? message;
    try {
      message = await body();
    } on ApiError catch (e) {
      message = e.message;
    } finally {
      if (mounted) setState(() => _busy = false);
      _key.currentState?.reload();
    }
    if (message != null && mounted) showApiMessage(context, message);
  }

  /// One approval. Returns the error rather than showing it, because the
  /// batch above it has to branch on the code.
  Future<ApiError?> _push(
    Map<String, dynamic> row, {
    bool ignoreMinimum = false,
  }) async {
    try {
      await _repo.approveRegistration(
        row['id'] as String,
        // Which step this row is on decides which step is being taken. A
        // mixed batch is possible — the queue is filtered by status, but a
        // reload between decisions is not — so it is read per row.
        asHod: row['status'] == 'ADVISER_APPROVED',
        ignoreMinimum: ignoreMinimum,
      );
      return null;
    } on ApiError catch (e) {
      return e;
    }
  }

  /// Approve one row, or a student's whole waiting load.
  ///
  /// The credit floor is asked once for the batch and not once per course:
  /// it is a property of the term's total registration, so it answers the
  /// same for every remaining row. Declining it stops the batch rather than
  /// grinding out the identical refusal seven more times.
  Future<void> _approve(List<Map<String, dynamic>> rows) async {
    // The HOD step is the one write on this screen with no way back: `reopen`
    // undoes a refusal, and nothing undoes an approval. One row is a deliberate
    // tap on one course; a whole load signed in one tap is worth asking about.
    if (rows.length > 1 && rows.any((r) => r['status'] == 'ADVISER_APPROVED')) {
      if (await _confirmFinal(rows.length) != true) return;
    }
    return _run(() async {
      var ignoreMinimum = false;
      final failed = <String>[];
      // Rows that got a decision, so a batch stopped halfway does not report
      // the ones it never reached as failures.
      var decided = 0;
      for (final row in rows) {
        var error = await _push(row, ignoreMinimum: ignoreMinimum);
        if (error != null && error.code == 'CREDIT_MINIMUM_NOT_MET') {
          if (!mounted) return null;
          if (await _confirmBelowMinimum(error) != true) {
            // Say what the batch did before it stopped: declining on the first
            // row used to leave "Approve all" looking like an ignored tap.
            return '${decided - failed.length} of ${rows.length} approved.';
          }
          ignoreMinimum = true;
          error = await _push(row, ignoreMinimum: true);
        }
        decided++;
        if (error != null) {
          failed.add('${row['subject_name'] ?? 'A course'} — ${error.message}');
        }
      }
      return failed.isEmpty ? null : _summarise(failed, decided);
    });
  }

  /// The last signature on a student's whole term, asked for once.
  Future<bool?> _confirmFinal(int count) => showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('Approve as head of department?'),
      content: Text(
        'All $count courses count towards the term and appear on the report '
        'card. There is no undo — only the student can drop a course, and '
        'only while the add/drop window is open.',
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context, false),
          child: const Text('Leave it'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, true),
          child: const Text('Approve all'),
        ),
      ],
    ),
  );

  /// One refusal reads in full; a batch of them would not fit a snackbar.
  String _summarise(
    List<String> failed,
    int total, [
    String did = 'approved',
  ]) => failed.length == 1
      ? failed.first
      : '${failed.length} of $total could not be $did.';

  /// The credit floor is the one refusal with a legitimate override: a
  /// final-year student with twelve units left to graduate looks exactly like
  /// an unfinished registration, and only a person can tell them apart.
  Future<bool?> _confirmBelowMinimum(ApiError error) => showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('Below the minimum load'),
      content: Text(
        '${error.message}\n\n'
        'Approve anyway only if this student has fewer courses left to take '
        'than the rule expects — a finalist, or a carry-over term.',
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context, false),
          child: const Text('Leave it'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, true),
          child: const Text('Approve anyway'),
        ),
      ],
    ),
  );

  /// Refuse one course, or a student's whole waiting load, on one reason.
  ///
  /// Asked once for the batch like the credit floor above it, and for the same
  /// reason: a semester is refused for one thing — the wrong term, a student
  /// who has withdrawn, a load nobody approved before registration closed —
  /// and typing it eight times is how a queue stays full.
  Future<void> _reject(List<Map<String, dynamic>> rows) async {
    if (rows.isEmpty) return;
    final reason = await showDialog<String>(
      context: context,
      builder: (context) => _ReasonDialog(
        subject: rows.length == 1
            ? (rows.first['subject_name'] as String? ?? 'this course')
            : '${rows.length} courses',
      ),
    );
    if (reason == null) return;
    await _run(() async {
      final failed = <String>[];
      for (final row in rows) {
        try {
          await _repo.rejectRegistration(row['id'] as String, reason: reason);
        } on ApiError catch (e) {
          failed.add('${row['subject_name'] ?? 'A course'} — ${e.message}');
        }
      }
      return failed.isEmpty ? null : _summarise(failed, rows.length, 'refused');
    });
  }

  /// Put a refusal back in the queue. No confirmation: it undoes a decision
  /// rather than making one, and the row lands back where it can be read
  /// before it is signed.
  Future<void> _reopen(List<Map<String, dynamic>> rows) => _run(() async {
    final failed = <String>[];
    for (final row in rows) {
      try {
        await _repo.reopenRegistration(row['id'] as String);
      } on ApiError catch (e) {
        failed.add('${row['subject_name'] ?? 'A course'} — ${e.message}');
      }
    }
    return failed.isEmpty ? null : _summarise(failed, rows.length, 'put back');
  });

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(title: const Text('Registrations')),
      body: Column(
        children: [
          if (_busy) const LinearProgressIndicator(),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              children: [
                if (_terms.isNotEmpty) ...[
                  DropdownButtonFormField<String?>(
                    initialValue: _termId,
                    isExpanded: true,
                    decoration: InputDecoration(
                      labelText: widget.session.label('TERM'),
                    ),
                    items: [
                      const DropdownMenuItem(
                        value: null,
                        child: Text('Every term'),
                      ),
                      for (final term in _terms)
                        DropdownMenuItem(
                          value: term['id'] as String,
                          child: Text(termLabel(term)),
                        ),
                    ],
                    onChanged: _busy
                        ? null
                        : (value) {
                            setState(() => _termId = value);
                            _key.currentState?.reload();
                          },
                  ),
                  const SizedBox(height: 12),
                ],
                // Wrap, not Row: both labels side by side overflow a narrow
                // phone.
                Align(
                  alignment: Alignment.centerLeft,
                  child: Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      for (final entry in _queue.entries)
                        ChoiceChip(
                          label: Text(entry.value),
                          selected: _status == entry.key,
                          // AsyncView loads once, so the filter change has to
                          // say so — setState alone leaves the old future on
                          // screen. Locked while a batch runs, like the term
                          // picker: reloading mid-write shows a queue that is
                          // half of what the writes are about to leave.
                          onSelected: _busy
                              ? null
                              : (_) {
                                  setState(() => _status = entry.key);
                                  _key.currentState?.reload();
                                },
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          Expanded(
            child: AsyncView<List<Map<String, dynamic>>>(
              key: _key,
              load: () => _repo.registrations(term: _termId, status: _status),
              empty: EmptyState(
                icon: Icons.task_alt_outlined,
                message: _status == _refused
                    ? 'Nothing has been refused.'
                    : 'Nothing waiting here.',
              ),
              builder: (context, rows) {
                final groups = groupByStudent(rows);
                return ListView.builder(
                  padding: const EdgeInsets.only(bottom: 12),
                  itemCount: groups.length,
                  itemBuilder: (context, index) => _StudentCard(
                    group: groups[index],
                    busy: _busy,
                    settled: _status == _refused,
                    onApprove: _approve,
                    onReject: _reject,
                    onReopen: _reopen,
                  ),
                );
              },
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Text(switch (_status) {
              'ADVISER_APPROVED' =>
                'Approving here is final: the course counts towards the term '
                    'and appears on the report card.',
              _refused =>
                'A refused course is not scored and does not count towards '
                    'the load. Putting one back sends it to the adviser again.',
              _ => 'An adviser approval passes it to the head of department.',
            }, style: text.bodySmall),
          ),
        ],
      ),
    );
  }
}

/// One student, their waiting courses, and the single button that signs off
/// all of them.
class _StudentCard extends StatelessWidget {
  const _StudentCard({
    required this.group,
    required this.busy,
    required this.settled,
    required this.onApprove,
    required this.onReject,
    required this.onReopen,
  });

  final RegistrationGroup group;
  final bool busy;

  /// Refused rows: nothing to sign, only a reason to read and an undo.
  final bool settled;

  final Future<void> Function(List<Map<String, dynamic>> rows) onApprove;
  final Future<void> Function(List<Map<String, dynamic>> rows) onReject;
  final Future<void> Function(List<Map<String, dynamic>> rows) onReopen;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final colors = Theme.of(context).colorScheme;
    final count = group.rows.length;
    return Card(
      margin: const EdgeInsets.fromLTRB(12, 6, 12, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          ListTile(
            title: Text(group.studentName, style: text.titleMedium),
            // "Waiting", not "registered": this is the filtered queue, not
            // the student's whole term load, and the credit floor is judged
            // on the latter.
            subtitle: Text(
              '$count course${count == 1 ? '' : 's'} '
              '${settled ? 'refused' : 'waiting'} · '
              '${group.creditUnits} units',
            ),
            trailing: count == 1
                ? null
                : Wrap(
                    spacing: 8,
                    children: settled
                        ? [
                            OutlinedButton(
                              onPressed: busy
                                  ? null
                                  : () => onReopen(group.rows),
                              child: const Text('Put all back'),
                            ),
                          ]
                        : [
                            TextButton(
                              onPressed: busy
                                  ? null
                                  : () => onReject(group.rows),
                              child: const Text('Refuse all'),
                            ),
                            FilledButton(
                              onPressed: busy
                                  ? null
                                  : () => onApprove(group.rows),
                              child: const Text('Approve all'),
                            ),
                          ],
                  ),
          ),
          const Divider(height: 1),
          for (final row in group.rows)
            ListTile(
              dense: true,
              title: Text(row['subject_name'] as String? ?? ''),
              subtitle: Text(
                [
                  '${row['credit_units'] ?? 0} units',
                  if (row['is_carryover'] == true) 'carry-over',
                  // The half the student was told and nobody here could see.
                  if (settled)
                    '“${row['rejection_reason'] as String? ?? 'no reason given'}”',
                ].join(' · '),
                style: settled
                    ? text.bodySmall?.copyWith(color: colors.onSurfaceVariant)
                    : null,
              ),
              trailing: settled
                  ? TextButton(
                      onPressed: busy ? null : () => onReopen([row]),
                      child: const Text('Put back'),
                    )
                  : Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        IconButton(
                          onPressed: busy ? null : () => onReject([row]),
                          icon: const Icon(Icons.close),
                          tooltip: 'Refuse',
                        ),
                        IconButton(
                          onPressed: busy ? null : () => onApprove([row]),
                          icon: const Icon(Icons.check),
                          tooltip: row['status'] == 'ADVISER_APPROVED'
                              ? 'Approve as HOD — this is final'
                              : 'Approve as adviser',
                        ),
                      ],
                    ),
            ),
        ],
      ),
    );
  }
}

/// A refusal the student will read, so it is required and it is theirs to
/// act on — "no" with no reason is a support call the office pays for.
class _ReasonDialog extends StatefulWidget {
  const _ReasonDialog({required this.subject});

  final String subject;

  @override
  State<_ReasonDialog> createState() => _ReasonDialogState();
}

class _ReasonDialogState extends State<_ReasonDialog> {
  final _reason = TextEditingController();

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text('Refuse ${widget.subject}'),
    content: TextField(
      controller: _reason,
      autofocus: true,
      maxLines: 3,
      maxLength: 200,
      decoration: const InputDecoration(
        labelText: 'Reason',
        helperText: 'The student sees this.',
      ),
      onChanged: (_) => setState(() {}),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: _reason.text.trim().isEmpty
            ? null
            : () => Navigator.pop(context, _reason.text.trim()),
        child: const Text('Refuse'),
      ),
    ],
  );
}

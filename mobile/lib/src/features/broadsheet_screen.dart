import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:printing/printing.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';

/// The exams office's grid: one class, every student down, every subject
/// across, with the term position at the end.
///
/// The pivot is the server's — `cells` on each row is already aligned with
/// `subjects`, so this file renders a table rather than assembling one. The
/// same sheet prints as a PDF, which is what actually goes on the wall.
class BroadsheetScreen extends StatefulWidget {
  const BroadsheetScreen({super.key, required this.session});

  final Session session;

  @override
  State<BroadsheetScreen> createState() => _BroadsheetScreenState();
}

class _BroadsheetScreenState extends State<BroadsheetScreen> {
  late final Repository _repo = Repository(widget.session.api);

  List<Map<String, dynamic>> _terms = const [];
  List<Map<String, dynamic>> _arms = const [];
  String? _termId;
  String? _armId;
  Map<String, dynamic>? _sheet;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadPickers();
  }

  Future<void> _loadPickers() async {
    setState(() => _busy = true);
    try {
      final terms = await _repo.terms();
      final arms = await _repo.classArms();
      if (!mounted) return;
      setState(() {
        _terms = terms;
        _arms = arms;
        // The term the school is in, not the first one ever created.
        _termId ??=
            terms.firstWhere(
                  (term) => term['is_current'] == true,
                  orElse: () => terms.isEmpty ? const {} : terms.first,
                )['id']
                as String?;
        _error = null;
      });
      await _load();
    } on ApiError catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _load() async {
    if (_termId == null) return;
    setState(() => _busy = true);
    try {
      final sheet = await _repo.broadsheet(term: _termId!, classArm: _armId);
      if (!mounted) return;
      setState(() {
        _sheet = sheet;
        _error = null;
      });
    } on ApiError catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _print() async {
    if (_termId == null) return;
    setState(() => _busy = true);
    try {
      final pdf = await _repo.broadsheetPdf(term: _termId!, classArm: _armId);
      await Printing.sharePdf(bytes: pdf, filename: 'broadsheet.pdf');
    } on ApiError catch (error) {
      if (mounted) showApiMessage(context, error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  List<Map<String, dynamic>> get _subjects =>
      (_sheet?['subjects'] as List? ?? const []).cast<Map<String, dynamic>>();

  List<Map<String, dynamic>> get _rows =>
      (_sheet?['rows'] as List? ?? const []).cast<Map<String, dynamic>>();

  /// Tertiary: the sheet closes with GPA and CGPA, not average and position.
  bool get _gradePoints => _sheet?['uses_grade_points'] == true;

  /// A level-wide tertiary sheet holds several programmes, so each row has to
  /// say which one it belongs to. One programme for all of them needs no column.
  bool get _mixedProgrammes => _sheet?['mixed_programmes'] == true;

  /// One cell of the grid, and what is behind it. A percentage in a column of
  /// forty says nothing on its own — the sheet shows what the mark is made of
  /// and offers the student's record, which is the next question every time.
  DataCell _cell(Map<String, dynamic> row, int index) {
    final cells = row['cells'] as List? ?? const [];
    final cell = index < cells.length ? cells[index] : null;
    return DataCell(
      Text(_mark(cell)),
      onTap: cell is! Map
          ? null
          : () => showModalBottomSheet<void>(
              context: context,
              builder: (sheetContext) => _CellSheet(
                row: row,
                subject: _subjects[index],
                cell: cell.cast<String, dynamic>(),
                session: widget.session,
                gradePoints: _gradePoints,
                onTranscript: () {
                  Navigator.pop(sheetContext);
                  _openTranscript(row);
                },
              ),
            ),
    );
  }

  void _openTranscript(Map<String, dynamic> row) {
    final student = row['student'];
    if (student != null) context.push('/transcript/$student');
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Broadsheet'),
      actions: [
        IconButton(
          onPressed: _busy || _sheet == null ? null : _print,
          icon: const Icon(Icons.print_outlined),
          tooltip: 'Print',
        ),
      ],
    ),
    body: Column(
      children: [
        Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            children: [
              DropdownButtonFormField<String>(
                initialValue: _termId,
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
                        setState(() => _termId = value);
                        _load();
                      },
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String?>(
                initialValue: _armId,
                isExpanded: true,
                decoration: InputDecoration(
                  labelText: widget.session.label('CLASS'),
                ),
                items: [
                  const DropdownMenuItem(
                    value: null,
                    child: Text('Every class'),
                  ),
                  for (final arm in _arms)
                    DropdownMenuItem(
                      value: arm['id'] as String?,
                      child: Text(
                        (arm['label'] ?? arm['name'] ?? '').toString(),
                      ),
                    ),
                ],
                onChanged: _busy
                    ? null
                    : (value) {
                        setState(() => _armId = value);
                        _load();
                      },
              ),
            ],
          ),
        ),
        if (_busy) const LinearProgressIndicator(),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.all(12),
            child: Text(
              _error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
        Expanded(
          child: _rows.isEmpty
              ? const EmptyState(
                  icon: Icons.grid_on_outlined,
                  message:
                      'Nothing to show yet.\nResults appear here once the term '
                      'has been computed.',
                )
              // Two scroll axes: forty students down, and as many subject
              // columns across as the class offers.
              : SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: SingleChildScrollView(
                    child: DataTable(
                      headingRowHeight: 40,
                      dataRowMinHeight: 36,
                      dataRowMaxHeight: 40,
                      columns: [
                        const DataColumn(label: Text('Student')),
                        if (_mixedProgrammes)
                          DataColumn(
                            label: Text(widget.session.label('CLASS_GROUP')),
                          ),
                        for (final subject in _subjects)
                          DataColumn(
                            label: Text(subject['code'] as String? ?? ''),
                            tooltip: subject['title'] as String?,
                            numeric: true,
                          ),
                        DataColumn(
                          label: Text(_gradePoints ? 'GPA' : 'Avg'),
                          numeric: true,
                        ),
                        DataColumn(
                          label: Text(_gradePoints ? 'CGPA' : 'Pos'),
                          numeric: true,
                        ),
                      ],
                      rows: [
                        for (final row in _rows)
                          DataRow(
                            cells: [
                              DataCell(
                                Text(row['full_name'] as String? ?? ''),
                                onTap: () => _openTranscript(row),
                              ),
                              if (_mixedProgrammes)
                                DataCell(Text(_text(row['programme']))),
                              for (
                                var index = 0;
                                index < _subjects.length;
                                index++
                              )
                                _cell(row, index),
                              DataCell(
                                Text(
                                  _number(
                                    _gradePoints ? row['gpa'] : row['average'],
                                  ),
                                ),
                              ),
                              DataCell(
                                Text(
                                  _number(
                                    _gradePoints
                                        ? row['cgpa']
                                        : row['position'],
                                  ),
                                ),
                              ),
                            ],
                          ),
                      ],
                    ),
                  ),
                ),
        ),
      ],
    ),
  );
}

/// What one cell of the grid is made of: whose mark, in which subject, out of
/// what, and where it placed in the class. The row already carries all of it —
/// this is a reading of the sheet, not a second request.
class _CellSheet extends StatelessWidget {
  const _CellSheet({
    required this.row,
    required this.subject,
    required this.cell,
    required this.session,
    required this.gradePoints,
    required this.onTranscript,
  });

  final Map<String, dynamic> row;
  final Map<String, dynamic> subject;
  final Map<String, dynamic> cell;
  final Session session;
  final bool gradePoints;
  final VoidCallback onTranscript;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(row['full_name'] as String? ?? '', style: text.titleMedium),
            Text(
              [
                row['admission_number'],
                subject['code'],
                subject['title'],
              ].where((part) => part != null && '$part'.isNotEmpty).join(' · '),
              style: text.bodySmall,
            ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 20,
              runSpacing: 12,
              children: [
                _Figure(label: 'Score', value: _number(cell['total'])),
                _Figure(
                  label: 'Percentage',
                  value: '${_number(cell['percentage'])}%',
                ),
                _Figure(label: 'Grade', value: _text(cell['grade'])),
                if (!gradePoints)
                  _Figure(
                    label: 'In ${session.label('SUBJECT').toLowerCase()}',
                    value: _number(cell['position']),
                  ),
                if (gradePoints) ...[
                  _Figure(label: 'GPA', value: _number(row['gpa'])),
                  _Figure(label: 'CGPA', value: _number(row['cgpa'])),
                ] else ...[
                  _Figure(
                    label: 'Term average',
                    value: _number(row['average']),
                  ),
                  _Figure(
                    label: 'Term position',
                    value: _number(row['position']),
                  ),
                ],
              ],
            ),
            const SizedBox(height: 16),
            Align(
              alignment: Alignment.centerRight,
              child: FilledButton.icon(
                onPressed: row['student'] == null ? null : onTranscript,
                icon: const Icon(Icons.history_edu_outlined),
                label: const Text('Transcript'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Figure extends StatelessWidget {
  const _Figure({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label, style: text.labelSmall),
        Text(value, style: text.titleMedium),
      ],
    );
  }
}

String _text(Object? value) {
  final string = '${value ?? ''}'.trim();
  return string.isEmpty ? '—' : string;
}

/// A subject cell: the percentage with its grade, or a dash where the student
/// does not take the subject at all — an empty cell reads as a zero.
String _mark(Object? cell) {
  if (cell is! Map) return '—';
  final grade = (cell['grade'] as String? ?? '').trim();
  final percentage = _number(cell['percentage']);
  return grade.isEmpty ? percentage : '$percentage $grade';
}

String _number(Object? value) {
  if (value == null) return '—';
  final number = double.tryParse('$value');
  if (number == null) return '$value';
  return number == number.roundToDouble()
      ? '${number.toInt()}'
      : number.toStringAsFixed(1);
}

import 'package:flutter/material.dart';
import 'package:printing/printing.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';

/// One student's whole record: every term they have a result for, oldest
/// first, with the CGPA and credit units the last one left behind.
///
/// Reached from the broadsheet, where a cell is one term of one subject and
/// the question it raises — "has this child always been like this?" — is
/// exactly what this screen answers. The printed version is the registrar's
/// signed sheet, so the print button only appears for the office.
class TranscriptScreen extends StatefulWidget {
  const TranscriptScreen({
    super.key,
    required this.session,
    required this.studentId,
  });

  final Session session;
  final String studentId;

  @override
  State<TranscriptScreen> createState() => _TranscriptScreenState();
}

class _TranscriptScreenState extends State<TranscriptScreen> {
  late final Repository _repo = Repository(widget.session.api);
  bool _busy = false;

  Future<void> _print() async {
    setState(() => _busy = true);
    try {
      final pdf = await _repo.transcriptPdf(widget.studentId);
      await Printing.sharePdf(bytes: pdf, filename: 'transcript.pdf');
    } on ApiError catch (error) {
      if (mounted) showApiMessage(context, error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Transcript'),
      actions: [
        if (widget.session.can('assessment.generate_report'))
          IconButton(
            onPressed: _busy ? null : _print,
            icon: _busy
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.picture_as_pdf_outlined),
            tooltip: 'Print',
          ),
      ],
    ),
    body: AsyncView<Map<String, dynamic>>(
      load: () => _repo.transcript(widget.studentId),
      builder: (context, data) {
        final terms = Repository.rows(data['terms']);
        if (terms.isEmpty) {
          return ListView(
            children: const [
              EmptyState(
                icon: Icons.history_edu_outlined,
                message:
                    'No results on record yet.\nA term appears here once it '
                    'has been computed.',
              ),
            ],
          );
        }
        final student = (data['student'] as Map?)?.cast<String, dynamic>();
        return ListView(
          padding: const EdgeInsets.all(12),
          children: [
            _Header(student: student, transcript: data),
            for (var index = 0; index < terms.length; index++)
              _TermCard(
                term: terms[index],
                session: widget.session,
                // The term they are in now is the one being asked about.
                open: index == terms.length - 1,
              ),
          ],
        );
      },
    ),
  );
}

class _Header extends StatelessWidget {
  const _Header({required this.student, required this.transcript});

  final Map<String, dynamic>? student;
  final Map<String, dynamic> transcript;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              student?['full_name'] as String? ?? 'Student',
              style: text.titleMedium,
            ),
            if ((student?['number'] as String? ?? '').isNotEmpty)
              Text(student!['number'] as String, style: text.bodySmall),
            const SizedBox(height: 12),
            Wrap(
              spacing: 16,
              runSpacing: 12,
              children: [
                _Metric(
                  label: 'Terms',
                  value: '${Repository.rows(transcript['terms']).length}',
                ),
                if (transcript['cgpa'] != null)
                  _Metric(label: 'CGPA', value: '${transcript['cgpa']}'),
                if (transcript['total_credit_units'] != null)
                  _Metric(
                    label: 'Credit units',
                    value: '${transcript['total_credit_units']}',
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// One term, collapsed to its summary until somebody wants the subjects. A
/// six-year record is thirty-six terms; expanded, that is a scroll nobody
/// finishes.
class _TermCard extends StatelessWidget {
  const _TermCard({
    required this.term,
    required this.session,
    required this.open,
  });

  final Map<String, dynamic> term;
  final Session session;
  final bool open;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final subjects = Repository.rows(term['subjects']);
    return Card(
      child: ExpansionTile(
        initiallyExpanded: open,
        shape: const Border(),
        title: Text('${term['term'] ?? ''} · ${term['session'] ?? ''}'),
        subtitle: Text(
          [
            term['level'],
            term['programme'],
            '${subjects.length} ${session.label('SUBJECT').toLowerCase()}s',
          ].where((part) => part != null && '$part'.isNotEmpty).join(' · '),
        ),
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
            child: Wrap(
              spacing: 16,
              runSpacing: 12,
              children: [
                if (term['average'] != null)
                  _Metric(label: 'Average', value: '${term['average']}%'),
                if (term['gpa'] != null)
                  _Metric(label: 'GPA', value: '${term['gpa']}'),
                if (term['cgpa'] != null)
                  _Metric(label: 'CGPA', value: '${term['cgpa']}'),
                if (term['credit_units_earned'] != null)
                  _Metric(
                    label: 'Units earned',
                    value: '${term['credit_units_earned']}',
                  ),
              ],
            ),
          ),
          for (final subject in subjects)
            ListTile(
              dense: true,
              title: Text(subject['code'] as String? ?? ''),
              subtitle: Text(subject['title'] as String? ?? ''),
              trailing: Text(
                [
                  if (subject['percentage'] != null)
                    '${subject['percentage']}%',
                  if ((subject['grade'] as String? ?? '').isNotEmpty)
                    subject['grade'],
                  // A polytechnic reads the point; a secondary school has none
                  // and would rather not see "null" where it isn't.
                  if (subject['grade_point'] != null)
                    '(${subject['grade_point']})',
                ].join(' '),
                style: text.titleSmall,
              ),
            ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}

class _Metric extends StatelessWidget {
  const _Metric({required this.label, required this.value});

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

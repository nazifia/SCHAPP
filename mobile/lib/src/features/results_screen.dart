import 'package:flutter/material.dart';
import 'package:printing/printing.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';

/// Term results for whoever is signed in: a student's own, a guardian's wards',
/// or — for the exams office — everybody's.
///
/// The list is scoped by the API, not here. A guardian's request simply comes
/// back with two children in it; nothing in this file decides that, which is
/// the point.
class ResultsScreen extends StatelessWidget {
  const ResultsScreen({super.key, required this.session});

  final Session session;

  @override
  Widget build(BuildContext context) {
    final repo = Repository(session.api);
    final term = session.label('TERM');
    return Scaffold(
      appBar: AppBar(title: Text('$term results')),
      body: AsyncView<List<Map<String, dynamic>>>(
        load: () => repo.termResults(),
        empty: EmptyState(
          icon: Icons.assignment_outlined,
          message:
              'No published results yet.\nThey appear here once the '
              'school publishes the $term.',
        ),
        builder: (context, results) => ListView.builder(
          padding: const EdgeInsets.all(12),
          itemCount: results.length,
          itemBuilder: (context, index) =>
              _ResultCard(result: results[index], session: session, repo: repo),
        ),
      ),
    );
  }
}

class _ResultCard extends StatelessWidget {
  const _ResultCard({
    required this.result,
    required this.session,
    required this.repo,
  });

  final Map<String, dynamic> result;
  final Session session;
  final Repository repo;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final position = result['position'];
    final cohort = result['cohort_size'];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(result['full_name'] as String? ?? '', style: text.titleMedium),
            // Tertiary only: which course of study this result is a result in,
            // and the department that owns it. Absent in a school.
            if (result['programme'] != null) ...[
              const SizedBox(height: 2),
              Text(
                [
                  result['programme'],
                  result['department'],
                ].where((part) => part != null).join(' · '),
                style: text.bodySmall,
              ),
            ],
            const SizedBox(height: 4),
            Text(
              '${result['subjects_count']} ${session.label('SUBJECT').toLowerCase()}s',
              style: text.bodySmall,
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 12,
              runSpacing: 12,
              children: [
                _Metric(label: 'Average', value: '${result['average']}%'),
                if (result['gpa'] != null)
                  _Metric(label: 'GPA', value: '${result['gpa']}'),
                if (result['cgpa'] != null)
                  _Metric(label: 'CGPA', value: '${result['cgpa']}'),
                // A GPA means a tertiary scale, where the standing is the GPA
                // and the cohort position is not published at all.
                if (position != null && result['gpa'] == null)
                  _Metric(
                    label: 'Position',
                    value: cohort == null
                        ? '$position'
                        : '$position of $cohort',
                  ),
                _Metric(
                  label: 'Attendance',
                  value: '${result['days_present']}/${result['days_total']}',
                ),
              ],
            ),
            if ((result['form_teacher_comment'] as String? ?? '')
                .isNotEmpty) ...[
              const Divider(height: 24),
              Text(
                result['form_teacher_comment'] as String,
                style: text.bodyMedium,
              ),
            ],
            if ((result['head_comment'] as String? ?? '').isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  result['head_comment'] as String,
                  style: text.bodyMedium,
                ),
              ),
            Align(
              alignment: Alignment.centerRight,
              child: _ReportCardButton(
                repo: repo,
                termResultId: result['id'] as String,
                // "Report card" in a school, "Result slip" in a polytechnic.
                label: session.label('REPORT'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Fetch the report card and hand it to the OS sheet — print, save or send it
/// to the parent on WhatsApp. Nothing is stored by the app.
class _ReportCardButton extends StatefulWidget {
  const _ReportCardButton({
    required this.repo,
    required this.termResultId,
    required this.label,
  });

  final Repository repo;
  final String termResultId;
  final String label;

  @override
  State<_ReportCardButton> createState() => _ReportCardButtonState();
}

class _ReportCardButtonState extends State<_ReportCardButton> {
  bool _busy = false;

  Future<void> _open() async {
    setState(() => _busy = true);
    try {
      final pdf = await widget.repo.reportCard(widget.termResultId);
      await Printing.sharePdf(bytes: pdf, filename: 'report-card.pdf');
    } on ApiError catch (error) {
      if (mounted) showApiMessage(context, error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => TextButton.icon(
    onPressed: _busy ? null : _open,
    icon: _busy
        ? const SizedBox(
            width: 16,
            height: 16,
            child: CircularProgressIndicator(strokeWidth: 2),
          )
        : const Icon(Icons.picture_as_pdf_outlined),
    label: Text(_busy ? 'Building…' : widget.label),
  );
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

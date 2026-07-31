import 'package:flutter/material.dart';

import '../api/repository.dart';
import '../auth/session.dart';
import '../design/async_view.dart';

/// The admin console: one screen, one request, the numbers a proprietor asks
/// for on a Monday morning — roll, staff, attendance rate, fee collection —
/// plus the list of overdue bills that is always the next question.
class ConsoleScreen extends StatelessWidget {
  const ConsoleScreen({super.key, required this.session});

  final Session session;

  @override
  Widget build(BuildContext context) {
    final repo = Repository(session.api);
    return Scaffold(
      appBar: AppBar(title: const Text('Console')),
      body: AsyncView<Map<String, dynamic>>(
        load: repo.overview,
        builder: (context, overview) {
          final students = (overview['students'] as Map? ?? {});
          final staff = (overview['staff'] as Map? ?? {});
          final attendance = (overview['attendance'] as Map? ?? {});
          final finance = (overview['finance'] as Map? ?? {});
          return ListView(
            padding: const EdgeInsets.all(12),
            children: [
              Text(
                '${overview['session'] ?? ''} · ${overview['term'] ?? 'no current term'}',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 12),
              // Extent, not a count off the window: the rail and the page cap
              // both shrink the box this grid is measured in.
              GridView(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                // ponytail: fixed tile height, not an aspect ratio — two
                // columns on a 320dp phone are 144dp wide, and 144/1.6 is
                // shorter than the number and its label.
                gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                  maxCrossAxisExtent: 240,
                  mainAxisExtent: 104,
                  mainAxisSpacing: 8,
                  crossAxisSpacing: 8,
                ),
                children: [
                  _Tile(
                    label: '${session.label('STUDENT')}s enrolled',
                    value: '${students['enrolled_this_session'] ?? 0}',
                  ),
                  _Tile(label: 'Staff', value: '${staff['active'] ?? 0}'),
                  _Tile(
                    label: 'Attendance',
                    value: '${attendance['rate'] ?? 0}%',
                  ),
                  _Tile(
                    label: 'Fees collected',
                    value: '${finance['collection_rate'] ?? 0}%',
                  ),
                ],
              ),
              const SizedBox(height: 16),
              Card(
                child: Column(
                  children: [
                    const ListTile(
                      leading: Icon(Icons.account_balance_wallet_outlined),
                      title: Text('Fees'),
                    ),
                    _Line('Billed', naira(finance['billed'])),
                    _Line('Collected', naira(finance['collected'])),
                    _Line('Outstanding', naira(finance['outstanding'])),
                    const SizedBox(height: 8),
                  ],
                ),
              ),
              const SizedBox(height: 8),
              Card(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    ListTile(
                      leading: const Icon(Icons.groups_outlined),
                      title: Text('${session.label('STUDENT')}s by class'),
                    ),
                    for (final row
                        in (students['by_level'] as List? ?? const [])
                            .cast<Map<String, dynamic>>())
                      _Line('${row['level__code']}', '${row['count']}'),
                    const SizedBox(height: 8),
                  ],
                ),
              ),
              const SizedBox(height: 8),
              _Defaulters(repo: repo),
            ],
          );
        },
      ),
    );
  }
}

class _Defaulters extends StatelessWidget {
  const _Defaulters({required this.repo});

  final Repository repo;

  @override
  Widget build(BuildContext context) => Card(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const ListTile(
          leading: Icon(Icons.warning_amber_outlined),
          title: Text('Overdue fees'),
          subtitle: Text('Biggest balance first'),
        ),
        FutureBuilder<List<Map<String, dynamic>>>(
          future: repo.defaulters(),
          builder: (context, snapshot) {
            if (!snapshot.hasData) {
              return const Padding(
                padding: EdgeInsets.all(16),
                child: LinearProgressIndicator(),
              );
            }
            final rows = snapshot.data!;
            if (rows.isEmpty) {
              return const Padding(
                padding: EdgeInsets.all(16),
                child: Text('Nothing overdue.'),
              );
            }
            return Column(
              children: [
                for (final row in rows.take(20))
                  ListTile(
                    dense: true,
                    title: Text(row['full_name'] as String? ?? ''),
                    subtitle: Text(
                      '${row['number']} · ${row['days_overdue']} days overdue',
                    ),
                    trailing: Text(naira(row['outstanding'])),
                  ),
              ],
            );
          },
        ),
      ],
    ),
  );
}

class _Tile extends StatelessWidget {
  const _Tile({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text(value, style: text.headlineSmall, maxLines: 1),
            const SizedBox(height: 4),
            // The label is the long one — "Students enrolled" wraps at this
            // width, and a school that renames its pupils wraps further.
            Flexible(
              child: Text(
                label,
                style: text.labelMedium,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Line extends StatelessWidget {
  const _Line(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
    child: Row(
      children: [
        Expanded(child: Text(label)),
        Text(value, style: Theme.of(context).textTheme.titleSmall),
      ],
    ),
  );
}

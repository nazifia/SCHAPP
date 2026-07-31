import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../api/cache.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';

/// The signed-in landing screen: what is waiting, and the two or three things
/// this particular person actually does.
///
/// The quick actions are driven by the permission codes the server sent with
/// `/auth/me/`, not by role names — a school that invents a "Head of Exams"
/// role gets the right buttons without a client release. Hiding a button is
/// courtesy; the API enforces every one of these again.
class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key, required this.session});

  final Session session;

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _Action {
  const _Action(this.path, this.icon, this.label, this.permission);

  final String path;
  final IconData icon;
  final String label;

  /// Null means everyone signed in sees it.
  final String? permission;
}

const _actions = [
  _Action(
    '/scores',
    Icons.edit_note_outlined,
    'Enter marks',
    'assessment.enter_score',
  ),
  _Action(
    '/attendance',
    Icons.how_to_reg_outlined,
    'Attendance',
    'attendance.mark',
  ),
  _Action('/results', Icons.assignment_outlined, 'Results', null),
  // The exams office's grid and its printable sheet — the same permission
  // that prints a transcript.
  _Action(
    '/broadsheet',
    Icons.grid_on_outlined,
    'Broadsheet',
    'assessment.generate_report',
  ),
  _Action('/fees', Icons.receipt_long_outlined, 'Fees', null),
  // The price list, which is where a fee is changed for everyone at once. The
  // Fees tile next to it is one family's bill at a time.
  _Action(
    '/fee-structures',
    Icons.price_change_outlined,
    'Fees & price lists',
    'finance.manage_fees',
  ),
  _Action('/notices', Icons.campaign_outlined, 'Notices', null),
  _Action(
    '/console',
    Icons.insights_outlined,
    'Console',
    'people.view_all_students',
  ),
  // Read is `view_student`, not `manage_student`: a teacher opens the register
  // to look up a pupil and the server hands them only their own classes.
  // Writing is gated inside the screen.
  _Action(
    '/students',
    Icons.school_outlined,
    'Students',
    'people.view_student',
  ),
  // Class lists and the seating of a promoted year group. `manage_enrolment`
  // and not `manage_structure`: creating a class is structure, deciding which
  // child sits in it is enrolment, and the registrar holds the second.
  _Action(
    '/classes',
    Icons.groups_outlined,
    'Classes',
    'academics.manage_enrolment',
  ),
  // The catalogue every timetable, mark sheet and registration hangs off.
  // `manage_structure` is the write permission; the screen is readable
  // without it, but nobody without it has a reason to open the list.
  _Action(
    '/subjects',
    Icons.menu_book_outlined,
    'Subjects',
    'academics.manage_structure',
  ),
  // The calendar, and the year groups every class, enrolment and promotion run
  // keys off. Opened at setup, then once a term to advance the term — which is
  // the one thing here that is not a rarity, and the reason the tile no longer
  // says "levels".
  _Action(
    '/structure',
    Icons.account_tree_outlined,
    'Calendar & structure',
    'academics.manage_structure',
  ),
  _Action(
    '/import',
    Icons.upload_file_outlined,
    'Import roster',
    'people.manage_student',
  ),
  _Action(
    '/id-cards',
    Icons.badge_outlined,
    'ID cards',
    'people.manage_student',
  ),
  // The adviser's and HOD's queue. Both steps hold the same permission, and
  // the screen splits them by the state each is waiting on.
  _Action(
    '/approvals',
    Icons.fact_check_outlined,
    'Registrations',
    'academics.approve_registration',
  ),
  // Whoever pays for the SMS is who gets to see whether it arrived.
  _Action(
    '/delivery',
    Icons.mark_email_read_outlined,
    'Message delivery',
    'communication.send_sms',
  ),
  // `view_staff` and not `manage_staff`: the screen is read-only without the
  // heavier permissions, and it is the only place that shows which staff have
  // no login at all.
  _Action(
    '/staff',
    Icons.manage_accounts_outlined,
    'Staff & access',
    'people.view_staff',
  ),
];

class _DashboardScreenState extends State<DashboardScreen> {
  late final Repository _repo = Repository(widget.session.api);
  List<OutboxEntry> _pending = const [];
  int _unread = 0;
  bool _syncing = false;

  @override
  void initState() {
    super.initState();
    _refreshQuietly();
  }

  Future<void> _refreshQuietly() async {
    final pending = await widget.session.store.pending();
    var unread = 0;
    try {
      unread = await _repo.unreadCount();
    } catch (_) {
      // Offline: an unread badge is not worth an error state.
    }
    if (mounted) {
      setState(() {
        _pending = pending;
        _unread = unread;
      });
    }
  }

  Future<void> _refresh() async {
    setState(() => _syncing = true);
    await syncPending(context, widget.session.api);
    try {
      await widget.session.reloadUser();
    } catch (_) {
      // Offline: the cached user is still the right one to show.
    }
    await _refreshQuietly();
    if (mounted) setState(() => _syncing = false);
  }

  @override
  Widget build(BuildContext context) {
    final session = widget.session;
    final text = Theme.of(context).textTheme;
    final actions = _actions
        .where((a) => a.permission == null || session.can(a.permission!))
        .toList();

    return Scaffold(
      appBar: AppBar(
        title: Text(session.tenant?.name ?? 'SCHAPP'),
        actions: [
          IconButton(
            onPressed: _syncing ? null : _refresh,
            icon: const Icon(Icons.sync),
            tooltip: 'Sync now',
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text('Welcome, ${session.fullName}', style: text.headlineSmall),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              children: [
                for (final role in session.roles) Chip(label: Text(role)),
              ],
            ),
            const SizedBox(height: 24),
            if (_pending.isNotEmpty)
              _PendingCard(
                pending: _pending,
                syncing: _syncing,
                onSync: _refresh,
              ),
            if (_unread > 0)
              Card(
                child: ListTile(
                  leading: const Icon(Icons.mark_email_unread_outlined),
                  title: Text(
                    '$_unread unread notice${_unread == 1 ? '' : 's'}',
                  ),
                  onTap: () => context.go('/notices'),
                ),
              ),
            const SizedBox(height: 8),
            GridView(
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              // ponytail: fixed tile height instead of aspect ratio - content is
              // a fixed 28px icon + label, so height must not follow width.
              // Max extent, not a column count off the window width: a rail
              // and the page cap both eat into the box this grid actually
              // gets, and the tile knows how wide it wants to be.
              gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                maxCrossAxisExtent: 220,
                mainAxisExtent: 112,
                mainAxisSpacing: 12,
                crossAxisSpacing: 12,
              ),
              children: [
                for (final action in actions)
                  Card(
                    clipBehavior: Clip.antiAlias,
                    child: InkWell(
                      onTap: () => context.go(action.path),
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Icon(action.icon, size: 28),
                            const SizedBox(height: 8),
                            // ponytail: Flexible so the label shrinks to the
                            // leftover space instead of overflowing the tile.
                            Flexible(
                              child: Text(
                                action.label,
                                style: text.titleSmall,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _PendingCard extends StatelessWidget {
  const _PendingCard({
    required this.pending,
    required this.syncing,
    required this.onSync,
  });

  final List<OutboxEntry> pending;
  final bool syncing;
  final VoidCallback onSync;

  @override
  Widget build(BuildContext context) => Card(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        ListTile(
          leading: const Icon(Icons.cloud_upload_outlined),
          title: Text(
            '${pending.length} change${pending.length == 1 ? '' : 's'} '
            'waiting to sync',
          ),
          subtitle: const Text('They will be sent when there is a connection.'),
        ),
        for (final entry in pending.take(5))
          ListTile(
            dense: true,
            leading: const Icon(Icons.schedule, size: 18),
            title: Text(entry.label.isEmpty ? entry.path : entry.label),
          ),
        Padding(
          padding: const EdgeInsets.all(8),
          child: TextButton(
            onPressed: syncing ? null : onSync,
            child: const Text('Sync now'),
          ),
        ),
      ],
    ),
  );
}

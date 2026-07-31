import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'auth/otp_screen.dart';
import 'auth/school_screen.dart';
import 'auth/session.dart';
import 'auth/signin_screen.dart';
import 'design/app_shell.dart';
import 'design/theme.dart';
import 'features/approvals_screen.dart';
import 'features/attendance_screen.dart';
import 'features/broadsheet_screen.dart';
import 'features/classes_screen.dart';
import 'features/console_screen.dart';
import 'features/delivery_screen.dart';
import 'features/fee_structures_screen.dart';
import 'features/fees_screen.dart';
import 'features/id_cards_screen.dart';
import 'features/import_screen.dart';
import 'features/notices_screen.dart';
import 'features/people_admin_screen.dart';
import 'features/registration_screen.dart';
import 'features/results_screen.dart';
import 'features/score_entry_screen.dart';
import 'features/structure_screen.dart';
import 'features/students_screen.dart';
import 'features/subjects_screen.dart';
import 'features/transcript_screen.dart';
import 'home/account_screen.dart';
import 'home/dashboard_screen.dart';

/// Navigation is permission-driven, so a teacher, a parent and a bursar get
/// three different apps out of one build. `permission` null means everyone.
class _Tab {
  const _Tab(this.item, [this.permission]);

  final NavItem item;
  final String? permission;
}

const _tabs = [
  _Tab(NavItem(path: '/', icon: Icons.home_outlined, label: 'Home')),
  _Tab(
    NavItem(path: '/scores', icon: Icons.edit_note_outlined, label: 'Marks'),
    'assessment.enter_score',
  ),
  _Tab(
    NavItem(
      path: '/attendance',
      icon: Icons.how_to_reg_outlined,
      label: 'Register',
    ),
    'attendance.mark',
  ),
  _Tab(
    NavItem(path: '/fees', icon: Icons.receipt_long_outlined, label: 'Fees'),
  ),
  _Tab(
    NavItem(path: '/notices', icon: Icons.campaign_outlined, label: 'Notices'),
  ),
  _Tab(NavItem(path: '/account', icon: Icons.person_outline, label: 'Account')),
];

List<NavItem> navItemsFor(Session session) => [
  for (final tab in _tabs)
    if (tab.permission == null || session.can(tab.permission!)) tab.item,
];

/// Routing plus the auth guard. Two gates, in order: a school must be chosen
/// before anything can be requested (the `X-Tenant-Slug` header is what
/// selects the tenant), then a user must be signed in.
///
/// `refreshListenable` is why sign-out needs no navigation code anywhere:
/// clearing the session notifies, the guard re-runs, and the app is back at
/// the sign-in screen.
GoRouter buildRouter(Session session, ThemeController theme) => GoRouter(
  refreshListenable: session,
  initialLocation: '/',
  redirect: (context, state) {
    final path = state.matchedLocation;
    final atSchool = path == '/school';
    final atSignIn = path.startsWith('/signin');

    if (session.tenant == null) return atSchool ? null : '/school';
    if (!session.signedIn) return atSignIn ? null : '/signin';
    if (atSchool || atSignIn) return '/';
    return null;
  },
  routes: [
    GoRoute(
      path: '/school',
      builder: (context, state) => SchoolScreen(session: session),
    ),
    GoRoute(
      path: '/signin',
      builder: (context, state) => SignInScreen(session: session),
      routes: [
        GoRoute(
          path: 'otp',
          // The challenge lives in `extra`, which a browser reload drops.
          // Without it there is no request_id to verify against.
          redirect: (context, state) => state.extra == null ? '/signin' : null,
          builder: (context, state) => OtpScreen(
            session: session,
            challenge: state.extra! as OtpChallenge,
          ),
        ),
      ],
    ),
    ShellRoute(
      builder: (context, state, child) => AppShell(
        // Rebuilt per navigation, so a role change on the next `/auth/me/`
        // is reflected without a restart.
        items: navItemsFor(session),
        currentPath: state.matchedLocation,
        offline: session.api.offline,
        onSelect: context.go,
        child: child,
      ),
      routes: [
        GoRoute(
          path: '/',
          builder: (context, state) => DashboardScreen(session: session),
        ),
        GoRoute(
          path: '/scores',
          // Leaving with a column keyed and unsent asks first — the screen's
          // own guard cannot see a tab tap, which is the way marks were
          // actually being lost.
          onExit: (context, state) => confirmDiscardMarks(context),
          builder: (context, state) => ScoreEntryScreen(session: session),
        ),
        GoRoute(
          path: '/attendance',
          builder: (context, state) => AttendanceScreen(session: session),
        ),
        GoRoute(
          path: '/results',
          builder: (context, state) => ResultsScreen(session: session),
        ),
        GoRoute(
          path: '/fees',
          builder: (context, state) => FeesScreen(session: session),
        ),
        // What a cohort is charged, as opposed to what one family owes. Off the
        // Fees screen and the dashboard, not the tab bar: a parent never
        // opens it, and a bursar opens it once a term.
        GoRoute(
          path: '/fee-structures',
          builder: (context, state) => FeeStructuresScreen(session: session),
        ),
        GoRoute(
          path: '/notices',
          builder: (context, state) => NoticesScreen(session: session),
        ),
        GoRoute(
          path: '/console',
          builder: (context, state) => ConsoleScreen(session: session),
        ),
        // Office jobs: reached from the dashboard, not the tab bar — a teacher
        // and a parent never open either one.
        GoRoute(
          path: '/import',
          builder: (context, state) => ImportScreen(session: session),
        ),
        GoRoute(
          path: '/broadsheet',
          builder: (context, state) => BroadsheetScreen(session: session),
        ),
        // One student's whole record. Pushed from a broadsheet cell, so it
        // takes the id in the path rather than `extra` — a reload keeps it.
        GoRoute(
          path: '/transcript/:student',
          builder: (context, state) => TranscriptScreen(
            session: session,
            studentId: state.pathParameters['student']!,
          ),
        ),
        GoRoute(
          path: '/id-cards',
          builder: (context, state) => IdCardsScreen(session: session),
        ),
        GoRoute(
          path: '/students',
          builder: (context, state) => StudentsScreen(session: session),
        ),
        GoRoute(
          path: '/classes',
          builder: (context, state) => ClassesScreen(session: session),
        ),
        GoRoute(
          path: '/subjects',
          builder: (context, state) => SubjectsScreen(session: session),
        ),
        GoRoute(
          path: '/structure',
          builder: (context, state) => StructureScreen(session: session),
        ),
        GoRoute(
          path: '/approvals',
          builder: (context, state) => ApprovalsScreen(session: session),
        ),
        GoRoute(
          path: '/registration',
          builder: (context, state) => RegistrationScreen(session: session),
        ),
        GoRoute(
          path: '/delivery',
          builder: (context, state) => DeliveryScreen(session: session),
        ),
        GoRoute(
          path: '/staff',
          builder: (context, state) => PeopleAdminScreen(session: session),
        ),
        GoRoute(
          path: '/account',
          builder: (context, state) =>
              AccountScreen(session: session, theme: theme),
        ),
      ],
    ),
  ],
);

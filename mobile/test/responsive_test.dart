import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:schapp/src/api/cache.dart';
import 'package:schapp/src/api/client.dart';
import 'package:schapp/src/auth/session.dart';
import 'package:schapp/src/design/app_shell.dart';
import 'package:schapp/src/design/theme.dart';
import 'package:schapp/src/features/fees_screen.dart';
import 'package:schapp/src/features/students_screen.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// The layout rules that only break on a window nobody develops on: a browser
/// at 1600px, and the 320dp phone still in a lot of school bags. Pins the
/// shape — which navigation, how many columns — not the pixels.
void main() {
  setUp(() {
    TestWidgetsFlutterBinding.ensureInitialized();
    SharedPreferences.setMockInitialValues({});
  });

  const items = [
    NavItem(path: '/', icon: Icons.home_outlined, label: 'Home'),
    NavItem(path: '/fees', icon: Icons.receipt_long_outlined, label: 'Fees'),
  ];

  void sizeTo(WidgetTester tester, Size size) {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
  }

  Future<void> pumpAt(WidgetTester tester, Size size, {Widget? page}) async {
    sizeTo(tester, size);
    await tester.pumpWidget(
      MaterialApp(
        theme: schappTheme(Brightness.light),
        home: AppShell(
          items: items,
          currentPath: '/',
          offline: ValueNotifier(false),
          onSelect: (_) {},
          child: page ?? const Scaffold(body: SizedBox.expand(key: Key('p'))),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  group('the shell', () {
    testWidgets('a phone gets the bottom bar and the full width', (
      tester,
    ) async {
      await pumpAt(tester, const Size(360, 800));

      expect(find.byType(NavigationBar), findsOneWidget);
      expect(find.byType(NavigationRail), findsNothing);
      expect(tester.getSize(find.byKey(const Key('p'))).width, 360);
    });

    testWidgets('a tablet swaps the bar for a rail', (tester) async {
      await pumpAt(tester, const Size(800, 1000));

      expect(find.byType(NavigationRail), findsOneWidget);
      expect(find.byType(NavigationBar), findsNothing);
    });

    testWidgets('a wide window caps the page instead of stretching it', (
      tester,
    ) async {
      await pumpAt(tester, const Size(1900, 1000));

      expect(
        tester.getSize(find.byKey(const Key('p'))).width,
        kContentMaxWidth,
      );
      // Centred in what is left after the rail, not pinned to one edge.
      final page = tester.getRect(find.byKey(const Key('p')));
      expect(1900 - page.right, greaterThan(100));
    });

    testWidgets('a 320dp phone lays the shell out without overflow', (
      tester,
    ) async {
      await pumpAt(tester, const Size(320, 640));

      expect(tester.takeException(), isNull);
    });

    // The cap and the split are set independently, and a screen loses its
    // second column silently if the cap ever drops below the split.
    testWidgets('a capped page is still wide enough to split in two', (
      tester,
    ) async {
      await pumpAt(tester, const Size(1900, 1000));

      final page = tester.getSize(find.byKey(const Key('p'))).width;
      expect(page, greaterThanOrEqualTo(kTwoColumnFrom));
    });

    // A rail plus 840 does not fit on an 800dp tablet, and it should not try.
    testWidgets('a tablet is one column, rail included', (tester) async {
      await pumpAt(tester, const Size(800, 1000));

      final page = tester.getSize(find.byKey(const Key('p'))).width;
      expect(page, lessThan(kTwoColumnFrom));
    });
  });

  // --- the two-column screens ----------------------------------------------

  const invoices = [
    {
      'id': 'inv-1',
      'number': 'INV-0001',
      'full_name': 'Ada Obi',
      'status': 'ISSUED',
      'balance': '5000.00',
      'amount_paid': '0.00',
      'total': '5000.00',
      'lines': [
        {'description': 'Tuition', 'amount': '5000.00'},
      ],
    },
    {
      'id': 'inv-2',
      'number': 'INV-0002',
      'full_name': 'Bola Eze',
      'status': 'ISSUED',
      'balance': '7000.00',
      'amount_paid': '0.00',
      'total': '7000.00',
      'lines': [
        {'description': 'Tuition', 'amount': '7000.00'},
      ],
    },
  ];

  const students = [
    {
      'id': 'stu-1',
      'full_name': 'Ada Obi',
      'display_number': 'KC/001',
      'status': 'ACTIVE',
    },
    {
      'id': 'stu-2',
      'full_name': 'Bola Eze',
      'display_number': 'KC/002',
      'status': 'ACTIVE',
    },
  ];

  /// Answers the handful of reads these two screens make and nothing else; a
  /// path this does not know about comes back as an empty list, which is what
  /// every list screen is built to survive.
  http.Client canned() => MockClient((request) async {
    final path = request.url.path;
    Object body = const {'results': <Object>[]};
    if (path.endsWith('/finance/invoices/')) {
      body = {'results': invoices};
    } else if (path.endsWith('/people/students/')) {
      body = {'results': students};
    } else if (path.contains('/people/students/stu-')) {
      body = students.first;
    }
    return http.Response(
      jsonEncode(body),
      200,
      headers: {'content-type': 'application/json'},
    );
  });

  Future<Session> session() async {
    final store = OfflineStore(await SharedPreferences.getInstance());
    return Session(
        api: ApiClient(store: store, httpClient: canned())..tenantSlug = 'kc',
        store: store,
      )
      ..user = {
        'full_name': 'Bursar',
        'permissions': ['*'],
      };
  }

  Future<void> pumpScreen(
    WidgetTester tester,
    Size size,
    Widget Function(Session) screen,
  ) async {
    sizeTo(tester, size);
    await tester.pumpWidget(
      MaterialApp(
        theme: schappTheme(Brightness.light),
        home: screen(await session()),
      ),
    );
    await tester.pumpAndSettle();
  }

  group('fees', () {
    // 'Total' is a row of the full invoice card, so counting it counts how
    // many bills are shown in full.
    testWidgets('a phone stacks every bill in full', (tester) async {
      await pumpScreen(
        tester,
        const Size(400, 900),
        (s) => FeesScreen(session: s),
      );

      expect(find.text('Total'), findsNWidgets(2));
    });

    testWidgets('a wide window shows a list and one bill', (tester) async {
      await pumpScreen(
        tester,
        const Size(1200, 900),
        (s) => FeesScreen(session: s),
      );

      expect(find.text('Total'), findsOneWidget);
      // Both are listed on the left; the first is also opened on the right,
      // so its number is the one that appears twice.
      expect(find.text('INV-0001'), findsNWidgets(2));
      expect(find.text('INV-0002'), findsOneWidget);

      await tester.tap(find.text('Bola Eze'));
      await tester.pumpAndSettle();
      expect(find.text('INV-0001'), findsOneWidget);
      expect(find.text('INV-0002'), findsNWidgets(2));
    });
  });

  group('students', () {
    testWidgets('a phone opens the record as a sheet', (tester) async {
      await pumpScreen(
        tester,
        const Size(400, 900),
        (s) => StudentsScreen(session: s),
      );

      expect(find.byType(BottomSheet), findsNothing);
      await tester.tap(find.text('Ada Obi'));
      await tester.pumpAndSettle();
      expect(find.byType(BottomSheet), findsOneWidget);
    });

    testWidgets('a wide window opens the record in the second column', (
      tester,
    ) async {
      await pumpScreen(
        tester,
        const Size(1200, 900),
        (s) => StudentsScreen(session: s),
      );

      expect(find.text('Pick a student to open their record.'), findsOneWidget);

      await tester.tap(find.text('Ada Obi'));
      await tester.pumpAndSettle();

      expect(find.text('Pick a student to open their record.'), findsNothing);
      expect(find.text('First name'), findsOneWidget);
      // A pane, not a route: nothing was pushed over the list.
      expect(find.byType(BottomSheet), findsNothing);
      expect(find.text('Bola Eze'), findsOneWidget);
    });

    // The form pops when it is a sheet and calls back when it is a pane —
    // popping from a pane would take the whole screen with it.
    testWidgets('closing the pane leaves the screen standing', (tester) async {
      await pumpScreen(
        tester,
        const Size(1200, 900),
        (s) => StudentsScreen(session: s),
      );

      await tester.tap(find.text('Ada Obi'));
      await tester.pumpAndSettle();
      // The buttons are the foot of a long form; scroll to them first.
      await tester.ensureVisible(find.text('Close'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Close'));
      await tester.pumpAndSettle();

      expect(find.text('Pick a student to open their record.'), findsOneWidget);
      expect(find.text('Bola Eze'), findsOneWidget);
    });
  });
}

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:schapp/src/api/cache.dart';
import 'package:schapp/src/api/client.dart';
import 'package:schapp/src/auth/session.dart';
import 'package:schapp/src/design/theme.dart';
import 'package:schapp/src/features/approvals_screen.dart';
import 'package:schapp/src/features/attendance_screen.dart';
import 'package:schapp/src/features/broadsheet_screen.dart';
import 'package:schapp/src/features/classes_screen.dart';
import 'package:schapp/src/features/console_screen.dart';
import 'package:schapp/src/features/delivery_screen.dart';
import 'package:schapp/src/features/fee_structures_screen.dart';
import 'package:schapp/src/features/fees_screen.dart';
import 'package:schapp/src/features/id_cards_screen.dart';
import 'package:schapp/src/features/import_screen.dart';
import 'package:schapp/src/features/notices_screen.dart';
import 'package:schapp/src/features/people_admin_screen.dart';
import 'package:schapp/src/features/results_screen.dart';
import 'package:schapp/src/features/score_entry_screen.dart';
import 'package:schapp/src/features/structure_screen.dart';
import 'package:schapp/src/features/students_screen.dart';
import 'package:schapp/src/features/subjects_screen.dart';
import 'package:schapp/src/features/transcript_screen.dart';
import 'package:schapp/src/home/account_screen.dart';
import 'package:schapp/src/home/dashboard_screen.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Every screen, drawn on the narrowest phone the app claims to support, with
/// the longest names a Nigerian school actually types. An overflow is an
/// exception in a test and nothing at all on a device except a black-and-yellow
/// stripe nobody files a bug for, so this is the only place it gets caught.
///
/// ponytail: one width, one assertion per screen. Golden files would pin the
/// pixels too, and then every copy change is a golden update.
void main() {
  setUp(() {
    TestWidgetsFlutterBinding.ensureInitialized();
    SharedPreferences.setMockInitialValues({});
  });

  /// 320dp is the floor Material names, and still the width of the phones in a
  /// lot of school bags.
  const phone = Size(320, 640);

  const longName = 'Oluwaseun Adebayo-Chukwuemeka Oyelaran';

  /// One row shaped like everything the screens read. A screen that wants a
  /// key nobody else has gets null and draws its placeholder, which is the
  /// layout that has to survive 320dp too.
  const row = {
    'id': 'row-1',
    'full_name': longName,
    'first_name': 'Oluwaseun',
    'last_name': 'Adebayo-Chukwuemeka',
    'student_name': longName,
    'staff_name': longName,
    'guardian_name': longName,
    'display_number': 'KC/2025/00091',
    'student_number': 'KC/2025/00091',
    'number': 'INV-2025-000091',
    'label': 'Senior Secondary 2 Gold',
    'name': 'Senior Secondary 2 Gold',
    'code': 'FURTHER-MATHEMATICS',
    'title': 'Further Mathematics and Statistics',
    'subject_name': 'Further Mathematics and Statistics',
    'subject_title': 'Further Mathematics and Statistics',
    'level_code': 'SSS2',
    'level_name': 'Senior Secondary 2',
    'status': 'PART_PAID',
    'state': 'PENDING',
    'channel': 'SMS',
    'recipient': '+2348030000000',
    'phone': '+2348030000000',
    'email': 'oluwaseun.adebayo@example.school.ng',
    'balance': '1250000.00',
    'amount_paid': '750000.00',
    'amount': '2000000.00',
    'total': '2000000.00',
    'credit_units': 4,
    'capacity': 40,
    'enrolled': 38,
    'roll_number': 7,
    'order': 3,
    'is_active': true,
    'score': '18.50',
    'max_score': '20.00',
    'weight': '20.00',
    'body':
        'Third term resumption is Monday. Fees must be cleared before then.',
    'message':
        'Third term resumption is Monday. Fees must be cleared before then.',
    'note': 'Sick note from home',
    'created_at': '2025-10-06T08:30:00Z',
    'sent_at': '2025-10-06T08:30:00Z',
    'date': '2025-10-06',
    'version': 1,
    'permissions': <String>['*'],
    'roles': <String>['school_admin'],
    'lines': [
      {'description': 'Tuition, boarding and books', 'amount': '2000000.00'},
    ],
    'scores': <String, Object>{},
    'components': <Object>[],
    'results': <Object>[],
  };

  /// Answers every read the same way: two of the row above. Screens that
  /// expect a single object read their keys off the envelope and fall back,
  /// which is a layout worth drawing at this width as well.
  http.Client canned() => MockClient((request) async {
    final rows = [
      row,
      {...row, 'id': 'row-2'},
    ];
    // The device list is read as a bare array rather than a page.
    final body = request.url.path.endsWith('/auth/devices/')
        ? rows
        : {'count': rows.length, 'results': rows};
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
        'full_name': longName,
        'tenant_name': 'Kings College Senior Secondary School, Lagos',
        'permissions': ['*'],
      };
  }

  /// The same phone turned sideways: 320dp of height, which is where a form or
  /// a stat row that assumed a tall window runs off the bottom.
  const landscape = Size(640, 320);

  /// Draws [build] at [size] and reports any layout exception. Tabs and
  /// pickers are part of the screen, so anything the first frame opens counts.
  Future<void> expectNoOverflow(
    WidgetTester tester,
    Widget Function(Session) build, {
    Size size = phone,
  }) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    // `takeException` reports the message and not the widget; without the dump
    // a failure here says "overflowed by 20 pixels" and leaves the file to
    // guesswork.
    final report = FlutterError.onError;
    FlutterError.onError = (details) {
      FlutterError.dumpErrorToConsole(details, forceReport: true);
      report?.call(details);
    };
    addTearDown(() => FlutterError.onError = report);

    await tester.pumpWidget(
      MaterialApp(
        theme: schappTheme(Brightness.light),
        home: build(await session()),
      ),
    );
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
  }

  final screens = <String, Widget Function(Session)>{
    'dashboard': (s) => DashboardScreen(session: s),
    'account': (s) => AccountScreen(session: s, theme: ThemeController()),
    'students': (s) => StudentsScreen(session: s),
    'classes': (s) => ClassesScreen(session: s),
    'subjects': (s) => SubjectsScreen(session: s),
    'structure': (s) => StructureScreen(session: s),
    'attendance': (s) => AttendanceScreen(session: s),
    'scores': (s) => ScoreEntryScreen(session: s),
    'broadsheet': (s) => BroadsheetScreen(session: s),
    'results': (s) => ResultsScreen(session: s),
    'transcript': (s) => TranscriptScreen(session: s, studentId: 'row-1'),
    'approvals': (s) => ApprovalsScreen(session: s),
    'fees': (s) => FeesScreen(session: s),
    'fee price lists': (s) => FeeStructuresScreen(session: s),
    'notices': (s) => NoticesScreen(session: s),
    'delivery': (s) => DeliveryScreen(session: s),
    'people': (s) => PeopleAdminScreen(session: s),
    'import': (s) => ImportScreen(session: s),
    'id cards': (s) => IdCardsScreen(session: s),
    'console': (s) => ConsoleScreen(session: s),
  };

  for (final entry in screens.entries) {
    testWidgets('${entry.key} fits a 320dp phone', (tester) async {
      await expectNoOverflow(tester, entry.value);
    });

    testWidgets('${entry.key} fits that phone sideways', (tester) async {
      await expectNoOverflow(tester, entry.value, size: landscape);
    });
  }
}

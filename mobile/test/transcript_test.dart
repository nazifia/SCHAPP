import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:schapp/src/api/cache.dart';
import 'package:schapp/src/api/client.dart';
import 'package:schapp/src/auth/session.dart';
import 'package:schapp/src/design/theme.dart';
import 'package:schapp/src/features/broadsheet_screen.dart';
import 'package:schapp/src/features/transcript_screen.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// The drilldown and the record behind it. The thing worth pinning is the
/// column alignment: `cells` is positional, so a student who does not take a
/// subject leaves a hole, and reading the list by position is the only way the
/// right mark lands under the right subject.
void main() {
  setUp(() {
    TestWidgetsFlutterBinding.ensureInitialized();
    SharedPreferences.setMockInitialValues({});
  });

  const sheet = {
    'term': 'First Term',
    'subjects': [
      {'id': 'sub-mth', 'code': 'MTH', 'title': 'Mathematics'},
      {'id': 'sub-eng', 'code': 'ENG', 'title': 'English'},
    ],
    'rows': [
      {
        'student': 'stu-1',
        'full_name': 'Ada Obi',
        'admission_number': 'KC/001',
        // No maths: the hole is the first column, so a client that reads the
        // list in order puts the English mark under MTH.
        'cells': [
          null,
          {'total': 71, 'percentage': 71, 'grade': 'B', 'position': 4},
        ],
        'average': 71,
        'position': 5,
      },
    ],
  };

  const transcript = {
    'student': {'id': 'stu-1', 'full_name': 'Ada Obi', 'number': 'KC/001'},
    'cgpa': '3.42',
    'total_credit_units': 24,
    'terms': [
      {
        'session': '2024/2025',
        'term': 'First Term',
        'level': 'SS2',
        'programme': null,
        'average': '68.0',
        'gpa': '3.20',
        'cgpa': '3.20',
        'credit_units_earned': 12,
        'subjects': [
          {
            'code': 'MTH',
            'title': 'Mathematics',
            'credit_units': 3,
            'percentage': '64.0',
            'grade': 'C',
            'grade_point': '3.00',
          },
        ],
      },
      {
        'session': '2024/2025',
        'term': 'Second Term',
        'level': 'SS2',
        'programme': null,
        'average': '74.0',
        'gpa': '3.60',
        'cgpa': '3.42',
        'credit_units_earned': 12,
        'subjects': [
          {
            'code': 'ENG',
            'title': 'English',
            'credit_units': 3,
            'percentage': '74.0',
            'grade': 'B',
            'grade_point': '4.00',
          },
        ],
      },
    ],
  };

  http.Client canned() => MockClient((request) async {
    final path = request.url.path;
    Object body = const {'results': <Object>[]};
    if (path.endsWith('/academics/terms/')) {
      body = {
        'results': [
          {'id': 'term-1', 'name': 'First Term', 'is_current': true},
        ],
      };
    } else if (path.endsWith('/assessment/broadsheet/')) {
      body = sheet;
    } else if (path.contains('/assessment/transcript/')) {
      body = transcript;
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
        'full_name': 'Exams officer',
        'permissions': ['*'],
      };
  }

  Future<void> pump(
    WidgetTester tester,
    Widget Function(Session) screen,
  ) async {
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      MaterialApp(
        theme: schappTheme(Brightness.light),
        home: screen(await session()),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('a cell opens the mark it actually belongs to', (tester) async {
    await pump(tester, (s) => BroadsheetScreen(session: s));

    // The subject with no mark reads as a dash and opens nothing.
    await tester.tap(find.text('—').first);
    await tester.pumpAndSettle();
    expect(find.text('Mathematics'), findsNothing);

    await tester.tap(find.text('71 B'));
    await tester.pumpAndSettle();
    // English, not Mathematics: the second cell belongs to the second column.
    expect(find.textContaining('English'), findsOneWidget);
    expect(find.text('Ada Obi'), findsWidgets);
    expect(find.text('Transcript'), findsOneWidget);
  });

  testWidgets('the transcript lists every term, newest expanded', (
    tester,
  ) async {
    await pump(tester, (s) => TranscriptScreen(session: s, studentId: 'stu-1'));

    expect(find.text('Ada Obi'), findsOneWidget);
    // The cumulative figures, which only the header carries.
    expect(find.text('24'), findsOneWidget);
    expect(find.text('3.42'), findsWidgets);
    expect(find.text('First Term · 2024/2025'), findsOneWidget);
    expect(find.text('Second Term · 2024/2025'), findsOneWidget);
    // The last term is open, the earlier one is not.
    expect(find.text('ENG'), findsOneWidget);
    expect(find.text('MTH'), findsNothing);
  });
}

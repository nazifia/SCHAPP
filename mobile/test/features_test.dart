import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:schapp/src/api/api_error.dart';
import 'package:schapp/src/api/cache.dart';
import 'package:schapp/src/api/client.dart';
import 'package:schapp/src/api/repository.dart';
import 'package:schapp/src/auth/session.dart';
import 'package:schapp/src/design/app_shell.dart';
import 'package:schapp/src/design/async_view.dart';
import 'package:schapp/src/features/approvals_screen.dart';
import 'package:schapp/src/features/attendance_screen.dart';
import 'package:schapp/src/features/classes_screen.dart';
import 'package:schapp/src/features/fee_structures_screen.dart';
import 'package:schapp/src/features/fees_screen.dart';
import 'package:schapp/src/features/registration_screen.dart';
import 'package:schapp/src/features/score_entry_screen.dart';
import 'package:schapp/src/features/structure_screen.dart';
import 'package:schapp/src/features/students_screen.dart';
import 'package:schapp/src/features/subjects_screen.dart';
import 'package:schapp/src/router.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  group('paginated reads', () {
    test('a cursor page yields its rows', () {
      final rows = Repository.rows({
        'next': 'cursor',
        'results': [
          {'id': '1'},
          {'id': '2'},
        ],
      });
      expect(rows.length, 2);
      expect(rows.first['id'], '1');
    });

    test('an action returning a bare list reads the same way', () {
      expect(
        Repository.rows([
          {'id': '1'},
        ]).length,
        1,
      );
    });

    test('anything else is empty, not an exception', () {
      // A 204, a cached null, an envelope we did not expect: a list screen
      // showing nothing beats a list screen crashing.
      expect(Repository.rows(null), isEmpty);
      expect(Repository.rows({'detail': 'nope'}), isEmpty);
    });
  });

  group('student write body', () {
    const arms = [
      {'id': 'arm-1', 'level': 'level-ss2', 'label': 'SS2 Gold'},
    ];

    test('the class carries its own level along', () {
      final body = studentPayload(
        firstName: ' Ada ',
        lastName: 'Obi',
        arm: 'arm-1',
        arms: arms,
      );
      expect(body['first_name'], 'Ada');
      expect(body['current_arm'], 'arm-1');
      // The form never asks for a level; a mismatch here would put the child
      // on the wrong broadsheet.
      expect(body['current_level'], 'level-ss2');
    });

    test('no class clears both keys rather than sending a stale level', () {
      final body = studentPayload(
        firstName: 'Ada',
        lastName: 'Obi',
        arms: arms,
      );
      expect(body['current_arm'], isNull);
      expect(body['current_level'], isNull);
    });

    test('a date of birth goes as a plain date, not a timestamp', () {
      final body = studentPayload(
        firstName: 'Ada',
        lastName: 'Obi',
        dateOfBirth: DateTime(2011, 3, 7),
      );
      expect(body['date_of_birth'], '2011-03-07');
    });
  });

  group('subject write body', () {
    test('the code is normalised so MTH101 is one row, not two', () {
      final body = subjectPayload(code: ' mth101 ', title: ' Mathematics ');
      expect(body['code'], 'MTH101');
      expect(body['title'], 'Mathematics');
    });

    test('a blank units box is zero — a secondary subject has no units', () {
      expect(subjectPayload(code: 'MTH', title: 'Maths')['credit_units'], 0);
      expect(
        subjectPayload(
          code: 'CSC201',
          title: 'Data Structures',
          creditUnits: '3',
        )['credit_units'],
        3,
      );
    });

    test('the optional keys are sent as null, not omitted', () {
      // A cleared level, stream or department has to reach the server as null
      // to clear the column; leaving the key out is a PATCH that keeps it.
      final body = subjectPayload(code: 'ENG', title: 'English');
      for (final key in ['level', 'stream', 'department']) {
        expect(body.containsKey(key), isTrue, reason: key);
        expect(body[key], isNull, reason: key);
      }
    });

    test('prerequisites go as the whole set, empty included', () {
      // The serializer replaces the relation with what it is sent, so an
      // empty list is the only way the last prerequisite is ever removed.
      expect(
        subjectPayload(code: 'CSC101', title: 'Intro')['prerequisites'],
        isEmpty,
      );
      expect(
        subjectPayload(
          code: 'CSC201',
          title: 'Data Structures',
          prerequisites: ['csc101', 'mth101'],
        )['prerequisites'],
        ['csc101', 'mth101'],
      );
    });

    test('a stream and a department go through as chosen', () {
      final body = subjectPayload(
        code: 'CHM',
        title: 'Chemistry',
        stream: 'stream-science',
        department: 'dept-science',
      );
      expect(body['stream'], 'stream-science');
      expect(body['department'], 'dept-science');
    });
  });

  group('naira', () {
    test('groups thousands the way Nigeria writes money', () {
      expect(naira('120000'), '₦120,000.00');
      expect(naira(1234567.5), '₦1,234,567.50');
      expect(naira('999'), '₦999.00');
    });

    test('a missing or unparseable amount reads as zero, not as NaN', () {
      expect(naira(null), '₦0.00');
      expect(naira(''), '₦0.00');
    });
  });

  group('term labels', () {
    test('a term reads as its session, not as three identical rows', () {
      expect(
        termLabel({'name': 'First Term', 'session_name': '2024/2025'}),
        'First Term · 2024/2025',
      );
    });

    test('a term with no session reads as its bare name', () {
      // An older server, or a picker already scoped to one session.
      expect(termLabel({'name': 'First Term'}), 'First Term');
      expect(
        termLabel({'name': 'First Term', 'session_name': ''}),
        'First Term',
      );
    });
  });

  group('the approval queue', () {
    const rows = [
      {
        'id': 'r1',
        'enrolment': 'e-ada',
        'student_name': 'Ada Obi',
        'subject_name': 'CSC101',
        'credit_units': 3,
      },
      {
        'id': 'r2',
        'enrolment': 'e-chidi',
        'student_name': 'Chidi Okeke',
        'subject_name': 'MTH101',
        'credit_units': 4,
      },
      {
        'id': 'r3',
        'enrolment': 'e-ada',
        'student_name': 'Ada Obi',
        'subject_name': 'MTH101',
        'credit_units': 4,
      },
    ];

    test('a student\'s scattered rows become one signature', () {
      final groups = groupByStudent(rows);
      expect(groups.map((g) => g.studentName), ['Ada Obi', 'Chidi Okeke']);
      expect(groups.first.rows.length, 2);
      expect(groups.first.creditUnits, 7);
    });

    test('two students of the same name stay two students', () {
      // Keyed on the enrolment, not the name: a namesake sharing a card
      // would be signed off by whoever approved the other one.
      final groups = groupByStudent([
        ...rows,
        {
          'id': 'r4',
          'enrolment': 'e-other-ada',
          'student_name': 'Ada Obi',
          'subject_name': 'PHY101',
          'credit_units': 2,
        },
      ]);
      expect(groups.length, 3);
      expect(groups.where((g) => g.studentName == 'Ada Obi').length, 2);
    });

    test('a row with no name or enrolment still lands somewhere', () {
      final groups = groupByStudent([
        {'id': 'r9'},
      ]);
      expect(groups.single.studentName, 'Unknown student');
      expect(groups.single.creditUnits, 0);
    });
  });

  group('the registration sheet', () {
    test('a settled course is only re-tickable when it can be retried', () {
      expect(canPick(null), isTrue);
      // Both of these keep their row and the server revives it.
      expect(canPick('REJECTED'), isTrue);
      expect(canPick('DROPPED'), isTrue);
      // These are already on the term; ticking one would write nothing.
      expect(canPick('SUBMITTED'), isFalse);
      expect(canPick('ADVISER_APPROVED'), isFalse);
      expect(canPick('APPROVED'), isFalse);
    });

    test('units add up, and an unpriced course is not a crash', () {
      expect(
        totalUnits([
          {'credit_units': 3},
          {'credit_units': '2'},
          {'id': 'no-units'},
        ]),
        5,
      );
    });
  });

  group('the invoice menu', () {
    Map<String, String> menuFor(
      String status, {
      double paid = 0,
      double balance = 50000,
      bool bursar = true,
    }) => invoiceMenu(
      status: status,
      paid: paid,
      balance: balance,
      manageFees: bursar,
      recordPayment: bursar,
    );

    test('a draft is issued before it can be paid', () {
      expect(menuFor('DRAFT').keys, contains('issue'));
      expect(menuFor('ISSUED').keys, isNot(contains('issue')));
    });

    test('cancelling comes off once money has landed', () {
      // The server refuses this until the payment is reversed, and an option
      // that only ever errors teaches people to ignore errors.
      expect(menuFor('ISSUED').keys, contains('cancel'));
      expect(
        menuFor('PART_PAID', paid: 20000, balance: 30000).keys,
        isNot(contains('cancel')),
      );
    });

    test('a settled bill takes no more money and no more decisions', () {
      final paid = menuFor('PAID', paid: 50000, balance: 0).keys;
      expect(paid, isNot(contains('pay')));
      expect(paid, isNot(contains('waive')));
      // Reversing stays: a receipt can bounce after the bill reads paid.
      expect(paid, contains('reverse'));
    });

    test('a parent gets the PDF and nothing else', () {
      expect(menuFor('ISSUED', bursar: false), {'document': 'Print invoice'});
    });

    test('adjusting needs its own permission, not the bursar\'s', () {
      expect(menuFor('ISSUED').keys, isNot(contains('adjust')));
      expect(
        invoiceMenu(
          status: 'ISSUED',
          paid: 0,
          balance: 50000,
          manageFees: false,
          recordPayment: false,
          adjustInvoice: true,
        ).keys,
        contains('adjust'),
      );
    });

    test('a paid bill can still be adjusted, a waived one cannot', () {
      Iterable<String> adjustable(String status) => invoiceMenu(
        status: status,
        paid: 50000,
        balance: 0,
        manageFees: true,
        recordPayment: true,
        adjustInvoice: true,
      ).keys;
      expect(adjustable('PAID'), contains('adjust'));
      expect(adjustable('WAIVED'), isNot(contains('adjust')));
      expect(adjustable('CANCELLED'), isNot(contains('adjust')));
    });
  });

  group('an adjustment', () {
    String? problem({
      List<String> amounts = const ['50000'],
      List<String> descriptions = const ['Tuition'],
      String discount = '0',
      String reason = 'Sibling rebate',
      double paid = 0,
    }) => adjustmentProblem(
      amounts: amounts,
      descriptions: descriptions,
      discount: discount,
      reason: reason,
      paid: paid,
    );

    test('needs a reason, a description and a number on every line', () {
      expect(problem(), isNull);
      expect(problem(reason: '  '), 'Say why this bill is changing.');
      expect(problem(descriptions: ['']), contains('description'));
      expect(problem(amounts: ['']), contains('amount'));
      expect(problem(amounts: ['-10']), contains('amount'));
      expect(problem(discount: 'free'), contains('discount'));
    });

    test('cannot go below the money already taken', () {
      // The server refuses this outright — ADJUSTMENT_BELOW_PAID — because a
      // bill under what was paid is a refund, which nothing here can pay out.
      expect(problem(amounts: ['30000'], paid: 40000), contains('40,000.00'));
      expect(problem(amounts: ['40000'], paid: 40000), isNull);
    });

    test('a discount past the subtotal floors at zero rather than failing', () {
      expect(problem(amounts: ['50000'], discount: '60000'), isNull);
      expect(
        problem(amounts: ['50000'], discount: '60000', paid: 100),
        contains('100.00'),
      );
    });
  });

  group('a price list edit', () {
    test('needs a name and a number that is not negative', () {
      expect(feeItemProblem(name: 'Tuition', amount: '50000'), isNull);
      // Zero is meant sometimes — a line kept on the list for the year it is
      // not charged — and only a negative one is a refund.
      expect(feeItemProblem(name: 'Tuition', amount: '0'), isNull);
      expect(feeItemProblem(name: '  ', amount: '50000'), contains('name'));
      expect(feeItemProblem(name: 'Bus', amount: 'free'), contains('number'));
      expect(feeItemProblem(name: 'Bus', amount: '-10'), contains('negative'));
    });

    test('sends a blank due-in-days as the model default, not zero', () {
      // Zero would make every bill overdue the day it is issued.
      final payload = feeStructurePayload(name: 'First Term', session: 's1');
      expect(payload['due_in_days'], 21);
      expect(payload['term'], isNull);
      expect(payload['level'], isNull);
      expect(
        feeStructurePayload(
          name: 'First Term',
          session: 's1',
          dueInDays: '30',
        )['due_in_days'],
        30,
      );
    });

    test('keeps the item scoped to its structure', () {
      final payload = feeItemPayload(
        structure: 'st1',
        name: ' Tuition ',
        amount: ' 50000 ',
        category: 'TUITION',
        order: 2,
      );
      expect(payload['structure'], 'st1');
      expect(payload['name'], 'Tuition');
      expect(payload['amount'], '50000');
      expect(payload['is_optional'], false);
    });

    test('falls back to the compiled-in categories when the server sends '
        'nothing usable', () {
      expect(
        categoriesFrom([
          {'value': 'TUITION', 'label': 'Tuition'},
        ]),
        {'TUITION': 'Tuition'},
      );
      expect(categoriesFrom(const []), isEmpty);
      // A row without a value is not a choice; it must not become a null key
      // the dropdown then asserts on.
      expect(
        categoriesFrom([
          {'label': 'Tuition'},
        ]),
        isEmpty,
      );
    });
  });

  group('a refused delete', () {
    ApiError parse(String body) => ApiError.fromBody(409, body);

    test('reads back the counts the server refused on', () {
      final error = parse(
        '{"error":{"code":"DEPENDENTS_EXIST","message":"still in use",'
        '"details":{"dependents":{"subject registrations":240,"terms":3}}}}',
      );
      expect(error.dependents, {'subject registrations': 240, 'terms': 3});
    });

    test('is empty for every other error, so no screen shows a stray list', () {
      expect(
        parse('{"error":{"code":"ARM_FULL","message":"full"}}').dependents,
        isEmpty,
      );
      // A 409 whose details are not the shape we expect must not throw on the
      // way to a snackbar.
      expect(
        parse(
          '{"error":{"code":"DEPENDENTS_EXIST","message":"x",'
          '"details":{"dependents":"several"}}}',
        ).dependents,
        isEmpty,
      );
    });
  });

  group('the attendance register payload', () {
    const students = [
      {'id': 'a', 'first_name': 'Ada'},
      {'id': 'b', 'first_name': 'Bola'},
    ];
    final at = DateTime.utc(2025, 10, 6, 8, 30);

    List<Map<String, dynamic>> build({
      Map<String, String> marks = const {},
      Map<String, int> versions = const {},
      Map<String, String> notes = const {},
    }) => attendanceRows(
      students: students,
      marks: marks,
      versions: versions,
      notes: notes,
      recordedAt: at,
    );

    test('an untouched pupil goes as present', () {
      // The whole reason the screen is usable: forty pupils, two taps.
      final rows = build(marks: {'b': 'ABSENT'});
      expect(rows.first['status'], 'PRESENT');
      expect(rows.last['status'], 'ABSENT');
      expect(rows.first['recorded_at'], '2025-10-06T08:30:00.000Z');
    });

    test('a version is sent only for a record the device has seen', () {
      final rows = build(versions: {'a': 3});
      expect(rows.first['version'], 3);
      // A version the client never saw would be answered as a conflict rather
      // than the create this is.
      expect(rows.last.containsKey('version'), isFalse);
    });

    test('a stored note is sent back rather than blanked', () {
      // The server writes `note` from what arrives, so omitting it deletes the
      // reason somebody typed for the absence.
      final rows = build(notes: {'a': 'Sick note from home'});
      expect(rows.first['note'], 'Sick note from home');
      expect(rows.last['note'], '');
    });
  });

  group('the mark sheet batch', () {
    const sheetRows = [
      {
        'student': 'a',
        'scores': {
          'ca1': {'score': '12', 'version': 4},
        },
      },
      {'student': 'b', 'scores': {}},
    ];
    final at = DateTime.utc(2025, 10, 6, 8, 30);

    ScoreBatch build(Map<String, String> typed) => scoreBatch(
      sheetRows: sheetRows,
      componentId: 'ca1',
      maxScore: '20',
      typed: typed,
      recordedAt: at,
    );

    test('a blank is not a zero, and a stored mark carries its version', () {
      final batch = build({'a': '15', 'b': ''});
      expect(batch.ok, isTrue);
      expect(batch.rows.length, 1);
      expect(batch.rows.single['score'], '15');
      expect(batch.rows.single['version'], 4);
    });

    test('a fresh mark is sent without a version', () {
      final batch = build({'a': '12', 'b': '9'});
      final fresh = batch.rows.firstWhere((row) => row['student'] == 'b');
      expect(fresh.containsKey('version'), isFalse);
    });

    test('a mark above the component maximum never leaves the device', () {
      // The server rolls the whole batch back for one bad row, so forty good
      // marks used to bounce on one typo.
      final batch = build({'a': '95', 'b': '9'});
      expect(batch.ok, isFalse);
      expect(batch.problems['a'], 'Marked out of 20.');
      expect(batch.rows, isEmpty); // nothing sent until it is fixed
    });

    test('clearing a stored mark unmarks it rather than doing nothing', () {
      // Blanking the field used to be a no-op: the mark keyed against the
      // wrong pupil stayed on the server whatever the teacher did here.
      final batch = build({'a': '', 'b': ''});
      expect(batch.ok, isTrue);
      expect(batch.removals, 1); // only 'a' had a mark stored
      expect(batch.rows.single['score'], isNull);
      expect(batch.rows.single['version'], 4); // versioned like an edit
    });

    test('an unparseable mark is named rather than dropped', () {
      // It was silently skipped before: the teacher was told the class saved
      // while that pupil's mark was never in the request.
      final batch = build({'a': '1.2.3'});
      expect(batch.problems['a'], 'That is not a number.');
    });
  });

  group('leaving the mark sheet', () {
    tearDown(() => unsavedMarks.value = false);

    /// The answer lands here whenever the guard settles; null while it asks.
    bool? allowed;

    Future<void> leave(WidgetTester tester) async {
      allowed = null;
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => TextButton(
              onPressed: () async =>
                  allowed = await confirmDiscardMarks(context),
              child: const Text('go'),
            ),
          ),
        ),
      );
      await tester.tap(find.text('go'));
      await tester.pumpAndSettle();
    }

    testWidgets('nothing keyed leaves without a question', (tester) async {
      await leave(tester);
      expect(allowed, isTrue);
      expect(find.byType(AlertDialog), findsNothing);
    });

    testWidgets('keyed marks are not lost to a tab tap', (tester) async {
      // The screen guarded a column switch and the tab bar guarded nothing,
      // so the quickest way out of forty unsent marks was the unguarded one.
      unsavedMarks.value = true;
      await leave(tester);
      expect(allowed, isNull); // still asking
      expect(find.text('Keep marking'), findsOneWidget);

      await tester.tap(find.text('Keep marking'));
      await tester.pumpAndSettle();
      expect(allowed, isFalse); // the tab tap is refused, marks stay
    });

    testWidgets('discard is taken at its word', (tester) async {
      unsavedMarks.value = true;
      await leave(tester);
      await tester.tap(find.text('Discard'));
      await tester.pumpAndSettle();
      expect(allowed, isTrue);
    });
  });

  group('class placement', () {
    const arm = {
      'id': 'arm-gold',
      'label': 'SS2 Gold',
      'level': 'level-ss2',
      'capacity': 40,
      'enrolled': 12,
    };

    const waiting = [
      {'id': 'e-ada', 'student_name': 'Ada Obi', 'level': 'level-ss2'},
      {'id': 'e-chidi', 'student_name': 'Chidi Okeke', 'level': 'level-ss1'},
    ];

    test('only the class\'s own level can be ticked', () {
      // The server refuses the rest with ARM_MISMATCH. Offering the tick and
      // reading the refusal afterwards is the same information, one round
      // trip and one confused registrar later.
      final eligible = eligibleFor(waiting, arm);
      expect(eligible.map((row) => row['id']), ['e-ada']);
    });

    test('no class chosen offers nobody', () {
      expect(eligibleFor(waiting, null), isEmpty);
    });

    test('the caption carries the seats, and survives without them', () {
      expect(armCaption(arm), 'SS2 Gold · 12 of 40');
      // `enrolled` is annotated against the current session; a school with no
      // current session gets the label alone rather than "null of 40".
      expect(armCaption({'label': 'SS2 Gold', 'capacity': 40}), 'SS2 Gold');
    });

    test('a retired class says so, seats or no seats', () {
      // It is only in the picker at all so somebody can bring it back.
      expect(
        armCaption({'label': 'SS2 Gold', 'capacity': 40, 'is_active': false}),
        'SS2 Gold · retired',
      );
    });
  });

  group('class write body', () {
    test('a blank capacity is the model default, not a class of nobody', () {
      expect(armPayload(level: 'l-ss2', name: 'Gold')['capacity'], 40);
      expect(
        armPayload(level: 'l-ss2', name: 'Gold', capacity: '45')['capacity'],
        45,
      );
    });

    test('the name is trimmed', () {
      expect(armPayload(level: 'l-ss2', name: ' Gold ')['name'], 'Gold');
    });

    test('a cleared stream or form teacher is sent as null, not omitted', () {
      // Leaving the key out is a PATCH that keeps whoever was there.
      final body = armPayload(level: 'l-jss1', name: 'A');
      for (final key in ['stream', 'form_teacher']) {
        expect(body.containsKey(key), isTrue, reason: key);
        expect(body[key], isNull, reason: key);
      }
    });
  });

  group('a year group of classes in one box', () {
    test('commas separate, spaces do not', () {
      // "Gold Star" is one class with a two-word name.
      expect(armNames('A, B, Gold'), ['A', 'B', 'Gold']);
      expect(armNames('Gold Star'), ['Gold Star']);
    });

    test('one name is still one class', () {
      expect(armNames('  Gold  '), ['Gold']);
    });

    test('a repeat is dropped however it is cased', () {
      // The unique constraint is on the exact name, so the database would
      // take "gold" and "Gold" as two rows nobody can tell apart.
      expect(armNames('Gold, gold, GOLD, Silver'), ['Gold', 'Silver']);
    });

    test('empty and separators alone name nothing', () {
      expect(armNames(''), isEmpty);
      expect(armNames(' , , '), isEmpty);
    });
  });

  group('level write body', () {
    test('the code is normalised so JSS1 is one level, not two', () {
      expect(levelPayload(code: ' jss1 ', name: 'Junior 1')['code'], 'JSS1');
    });

    test('a blank name falls back to the code', () {
      final body = levelPayload(code: 'NUR1', name: '  ');
      expect(body['name'], 'NUR1');
    });

    test('a blank order takes the place offered, not zero', () {
      // Zero would sort a new level in front of the first year of the school.
      expect(
        levelPayload(code: 'SSS4', name: '', fallbackOrder: 7)['order'],
        7,
      );
      expect(levelPayload(code: 'SSS4', name: '', order: '3')['order'], 3);
    });

    test('a final year promotes nobody', () {
      // Both set is a row that says graduate and go on to SSS3 at once, and
      // which one the promotion run does is an implementation detail.
      final body = levelPayload(
        code: 'SSS3',
        name: 'SSS 3',
        nextLevel: 'level-sss1',
        isTerminal: true,
      );
      expect(body['next_level'], isNull);
      expect(body['is_terminal'], isTrue);
    });

    test('an ordinary level keeps the chain it was given', () {
      expect(
        levelPayload(code: 'JSS1', name: '', nextLevel: 'level-jss2'),
        containsPair('next_level', 'level-jss2'),
      );
    });
  });

  group('stream write body', () {
    test('a new stream gets a slug the server will accept', () {
      expect(streamPayload(name: ' Further Maths ')['code'], 'further-maths');
      expect(streamPayload(name: ' Further Maths ')['name'], 'Further Maths');
    });

    test('an edit keeps the code subjects and classes point at', () {
      final body = streamPayload(name: 'Pure Science', code: 'science');
      expect(body['code'], 'science');
      expect(body['name'], 'Pure Science');
    });

    test('punctuation does not leave a hanging hyphen', () {
      expect(slug('Arts & Humanities!'), 'arts-humanities');
      expect(slug('  '), isEmpty);
    });
  });

  group('calendar write bodies', () {
    test('dates go out as the plain days the server takes', () {
      final body = sessionPayload(
        name: ' 2026/2027 ',
        start: DateTime(2026, 9, 1),
        end: DateTime(2027, 7, 31),
      );
      expect(body['name'], '2026/2027');
      expect(body['start_date'], '2026-09-01');
      expect(body['end_date'], '2027-07-31');
    });

    test('a blank term name falls back to its ordinal', () {
      final body = termPayload(
        session: 'session-1',
        index: 2,
        name: '   ',
        start: DateTime(2027, 1, 8),
        end: DateTime(2027, 4, 5),
      );
      expect(body['name'], 'Second Term');
      expect(body['index'], 2);
      expect(body['session'], 'session-1');
    });

    test('a polytechnic gets semesters, and nothing here decides that', () {
      // The word comes from the tenant's labels — `labels_for` on the server.
      expect(termName(2, 'Semester'), 'Second Semester');
      expect(
        termPayload(
          session: 'session-1',
          index: 1,
          name: '',
          start: DateTime(2026, 9, 1),
          end: DateTime(2027, 1, 31),
          termLabel: 'Semester',
        )['name'],
        'First Semester',
      );
    });

    test('a name the school typed survives', () {
      expect(
        termPayload(
          session: 'session-1',
          index: 1,
          name: 'Harmattan',
          start: DateTime(2026, 9, 1),
          end: DateTime(2027, 1, 31),
        )['name'],
        'Harmattan',
      );
    });

    test('the current row is the one flagged, or none', () {
      const rows = [
        {'id': 'a', 'is_current': false},
        {'id': 'b', 'is_current': true},
      ];
      expect(currentRow(rows)?['id'], 'b');
      expect(
        currentRow(const [
          {'id': 'a'},
        ]),
        isNull,
      );
      expect(currentRow(const []), isNull);
    });

    test('nothing picked shows the current year', () {
      const rows = [
        {'id': 'a', 'is_current': false},
        {'id': 'b', 'is_current': true},
      ];
      expect(sessionInView(rows, null)?['id'], 'b');
      expect(sessionInView(rows, 'a')?['id'], 'a');
    });

    test('a picked year that has gone falls back to the current one', () {
      // Another office deletes a mis-keyed session, or this phone was offline
      // while it happened: an empty term list would read as "none keyed".
      const rows = [
        {'id': 'b', 'is_current': true},
      ];
      expect(sessionInView(rows, 'deleted')?['id'], 'b');
      expect(sessionInView(const [], 'deleted'), isNull);
    });
  });

  group('the calendar section of the structure screen', () {
    setUp(() {
      TestWidgetsFlutterBinding.ensureInitialized();
      SharedPreferences.setMockInitialValues({});
    });

    /// Two sessions, two terms in the current one and one in next year, plus a
    /// record of the writes and of which year's terms were asked for.
    Future<(Session, List<http.Request>)> screenSession({
      List<String> permissions = const ['academics.manage_structure'],
      List<String>? termQueriesOut,
    }) async {
      final sent = <http.Request>[];
      final termQueries = termQueriesOut ?? <String>[];
      final store = OfflineStore(await SharedPreferences.getInstance());
      final client = MockClient((request) async {
        final path = request.url.path;
        if (request.method == 'POST') {
          sent.add(request);
          return http.Response('{"id":"x","is_current":true}', 200);
        }
        if (path.endsWith('/academics/sessions/')) {
          return http.Response(
            '[{"id":"s-2025","name":"2025/2026","start_date":"2025-09-01",'
            '"end_date":"2026-07-31","is_current":true},'
            '{"id":"s-2026","name":"2026/2027","start_date":"2026-09-01",'
            '"end_date":"2027-07-31","is_current":false}]',
            200,
          );
        }
        if (path.endsWith('/academics/terms/')) {
          // The screen asks for one session's terms, never all of them.
          final asked = request.url.queryParameters['session'];
          termQueries.add(asked ?? '');
          if (asked == 's-2026') {
            return http.Response(
              '[{"id":"t-next","name":"First Term","index":1,'
              '"start_date":"2026-09-01","end_date":"2026-12-12",'
              '"is_current":false}]',
              200,
            );
          }
          return http.Response(
            '[{"id":"t-1","name":"First Term","index":1,'
            '"start_date":"2025-09-01","end_date":"2025-12-12",'
            '"is_current":true},'
            '{"id":"t-2","name":"Second Term","index":2,'
            '"start_date":"2026-01-06","end_date":"2026-04-03",'
            '"is_current":false}]',
            200,
          );
        }
        return http.Response('[]', 200);
      });
      final session = Session(
        api: ApiClient(store: store, httpClient: client),
        store: store,
      )..user = {'full_name': 'Principal', 'permissions': permissions};
      return (session, sent);
    }

    testWidgets('the year and the term the school is in are both shown', (
      tester,
    ) async {
      final (session, _) = await screenSession();
      await tester.pumpWidget(
        MaterialApp(home: StructureScreen(session: session)),
      );
      await tester.pumpAndSettle();

      expect(find.textContaining('The school is in 2025/2026'), findsOneWidget);
      expect(find.text('First Term'), findsOneWidget);
      expect(find.text('Second Term'), findsOneWidget);
      // One chip per section: the current session and the current term.
      expect(find.widgetWithText(Chip, 'Current'), findsNWidgets(2));
    });

    testWidgets('advancing the term confirms first, then posts', (
      tester,
    ) async {
      final (session, sent) = await screenSession();
      await tester.pumpWidget(
        MaterialApp(home: StructureScreen(session: session)),
      );
      await tester.pumpAndSettle();

      // Second Term's button — First Term shows a chip, not one of these.
      await tester.tap(find.text('Make current').last);
      await tester.pumpAndSettle();
      expect(find.textContaining('Marks already entered stay'), findsOneWidget);
      expect(sent, isEmpty); // nothing written until it is confirmed

      await tester.tap(find.text('Move'));
      await tester.pumpAndSettle();

      expect(
        sent.single.url.path,
        endsWith('/academics/terms/t-2/set-current/'),
      );
    });

    testWidgets('cancelling the confirmation writes nothing', (tester) async {
      final (session, sent) = await screenSession();
      await tester.pumpWidget(
        MaterialApp(home: StructureScreen(session: session)),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('Make current').first);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();

      expect(sent, isEmpty);
    });

    testWidgets('rolling the year over posts to the session endpoint', (
      tester,
    ) async {
      final (session, sent) = await screenSession();
      await tester.pumpWidget(
        MaterialApp(home: StructureScreen(session: session)),
      );
      await tester.pumpAndSettle();

      // The first button on the page belongs to 2026/2027: the current
      // session carries a chip instead.
      await tester.tap(find.text('Make current').first);
      await tester.pumpAndSettle();
      expect(find.textContaining('moves with it'), findsOneWidget);
      await tester.tap(find.text('Move'));
      await tester.pumpAndSettle();

      expect(
        sent.single.url.path,
        endsWith('/academics/sessions/s-2026/set-current/'),
      );
    });

    testWidgets('picking another year loads that year\'s terms', (
      tester,
    ) async {
      final asked = <String>[];
      final (session, _) = await screenSession(termQueriesOut: asked);
      await tester.pumpWidget(
        MaterialApp(home: StructureScreen(session: session)),
      );
      await tester.pumpAndSettle();
      expect(asked, ['s-2025']); // opens on the year the school is in

      await tester.tap(find.text('2025/2026 (current)'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('2026/2027').last);
      await tester.pumpAndSettle();

      expect(asked.last, 's-2026');
      expect(find.textContaining('not the year the school is in'), findsOne);
      // Next year's only term, and last year's two are gone from the list.
      expect(find.text('Second Term'), findsNothing);
    });

    testWidgets('advancing into a year not yet started says the year moves', (
      tester,
    ) async {
      final (session, sent) = await screenSession();
      await tester.pumpWidget(
        MaterialApp(home: StructureScreen(session: session)),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.text('2025/2026 (current)'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('2026/2027').last);
      await tester.pumpAndSettle();

      // Two buttons now: the 2026/2027 session's, and its first term's.
      await tester.tap(find.text('Make current').last);
      await tester.pumpAndSettle();
      expect(
        find.textContaining('The school moves into 2026/2027 with it'),
        findsOne,
      );

      await tester.tap(find.text('Move'));
      await tester.pumpAndSettle();
      expect(
        sent.single.url.path,
        endsWith('/academics/terms/t-next/set-current/'),
      );
    });

    testWidgets('one session offers nothing to pick between', (tester) async {
      final store = OfflineStore(await SharedPreferences.getInstance());
      final client = MockClient((request) async {
        if (request.url.path.endsWith('/academics/sessions/')) {
          return http.Response(
            '[{"id":"s-2025","name":"2025/2026","start_date":"2025-09-01",'
            '"end_date":"2026-07-31","is_current":true}]',
            200,
          );
        }
        return http.Response('[]', 200);
      });
      final session =
          Session(
              api: ApiClient(store: store, httpClient: client),
              store: store,
            )
            ..user = {
              'full_name': 'Principal',
              'permissions': const ['academics.manage_structure'],
            };
      await tester.pumpWidget(
        MaterialApp(home: StructureScreen(session: session)),
      );
      await tester.pumpAndSettle();

      expect(find.text('The school is in 2025/2026'), findsOne);
      expect(find.byType(DropdownButtonFormField<String>), findsNothing);
    });

    testWidgets('a bursar sees the calendar and cannot move it', (
      tester,
    ) async {
      final (session, _) = await screenSession(
        permissions: const ['finance.view_invoice'],
      );
      await tester.pumpWidget(
        MaterialApp(home: StructureScreen(session: session)),
      );
      await tester.pumpAndSettle();

      expect(find.text('First Term'), findsOneWidget);
      expect(find.text('Make current'), findsNothing);
      expect(find.textContaining('Add'), findsNothing);
    });
  });

  group('the classes screen', () {
    setUp(() {
      TestWidgetsFlutterBinding.ensureInitialized();
      SharedPreferences.setMockInitialValues({});
    });

    /// Answers the three reads the screen opens with, and records the write.
    Future<(Session, List<http.Request>)> screenSession({
      int allocateStatus = 200,
      String allocateBody = '[]',
    }) async {
      final sent = <http.Request>[];
      final store = OfflineStore(await SharedPreferences.getInstance());
      final client = MockClient((request) async {
        final path = request.url.path;
        if (request.method == 'POST') {
          sent.add(request);
          return http.Response(allocateBody, allocateStatus);
        }
        if (path.endsWith('/arms/')) {
          return http.Response(
            '[{"id":"arm-gold","label":"SS2 Gold","level":"level-ss2",'
            '"capacity":40,"enrolled":1,"is_active":true}]',
            200,
          );
        }
        if (path.contains('/arms/arm-gold/students/')) {
          return http.Response(
            '[{"id":"e-bola","student_name":"Bola Ade",'
            '"student_number":"KC/25/0007","roll_number":1}]',
            200,
          );
        }
        return http.Response(
          '[{"id":"e-ada","student_name":"Ada Obi","student_number":'
          '"KC/25/0009","level":"level-ss2","level_code":"SS2"}]',
          200,
        );
      });
      final session =
          Session(
              api: ApiClient(store: store, httpClient: client),
              store: store,
            )
            ..user = {
              'full_name': 'Registrar',
              'permissions': ['academics.manage_enrolment'],
            };
      return (session, sent);
    }

    testWidgets('the register and the worklist arrive together', (
      tester,
    ) async {
      final (session, _) = await screenSession();
      await tester.pumpWidget(
        MaterialApp(home: ClassesScreen(session: session)),
      );
      await tester.pumpAndSettle();

      expect(find.text('Bola Ade'), findsOneWidget); // already seated
      expect(find.text('Ada Obi'), findsOneWidget); // waiting for a class
      expect(find.textContaining('1 waiting for a class'), findsOneWidget);
    });

    testWidgets('ticking a student sends them to the chosen class', (
      tester,
    ) async {
      final (session, sent) = await screenSession();
      await tester.pumpWidget(
        MaterialApp(home: ClassesScreen(session: session)),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.byType(CheckboxListTile));
      await tester.pumpAndSettle();
      await tester.tap(find.textContaining('Place 1 in'));
      await tester.pumpAndSettle();

      final write = sent.single;
      expect(write.url.path, endsWith('/academics/arms/arm-gold/allocate/'));
      final body = jsonDecode(write.body) as Map<String, dynamic>;
      expect(body['enrolments'], ['e-ada']);
      // The override is opt-in, and asked for only after a refusal.
      expect(body['enforce_capacity'], isTrue);
    });

    testWidgets('a full class is refused, then overfilled on purpose', (
      tester,
    ) async {
      final (session, sent) = await screenSession(
        allocateStatus: 400,
        allocateBody:
            '{"error":{"code":"ARM_FULL","message":"SS2 Gold is full (40/40).",'
            '"details":{"capacity":40,"enrolled":40}}}',
      );
      await tester.pumpWidget(
        MaterialApp(home: ClassesScreen(session: session)),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.byType(CheckboxListTile));
      await tester.pumpAndSettle();
      await tester.tap(find.textContaining('Place 1 in'));
      await tester.pumpAndSettle();

      expect(find.text('That class is full'), findsOneWidget);
      await tester.tap(find.text('Seat them anyway'));
      await tester.pumpAndSettle();

      // Two writes: the refused one, then the same batch without the check.
      expect(sent.length, 2);
      expect(jsonDecode(sent.last.body)['enforce_capacity'], isFalse);
    });

    testWidgets('a teacher who cannot enrol gets a read-only class list', (
      tester,
    ) async {
      final (session, _) = await screenSession();
      session.user = {'full_name': 'Sade', 'permissions': const <String>[]};
      await tester.pumpWidget(
        MaterialApp(home: ClassesScreen(session: session)),
      );
      await tester.pumpAndSettle();

      expect(find.text('Bola Ade'), findsOneWidget);
      expect(find.byType(CheckboxListTile), findsNothing);
      expect(find.text('Move'), findsNothing);
    });
  });

  group('permission-driven navigation', () {
    setUp(() {
      TestWidgetsFlutterBinding.ensureInitialized();
      SharedPreferences.setMockInitialValues({});
    });

    Future<Session> sessionFor(List<String> permissions) async {
      final store = OfflineStore(await SharedPreferences.getInstance());
      return Session(
        api: ApiClient(store: store),
        store: store,
      )..user = {'full_name': 'Head', 'permissions': permissions};
    }

    test('the school admin wildcard grants every code', () async {
      // `school_admin` carries `*` alone rather than an expansion of every
      // code, so a literal `contains` showed the proprietor a parent's app.
      final session = await sessionFor(['*']);
      expect(session.can('people.view_staff'), isTrue);
      expect(session.can('admin.manage_users'), isTrue);
      expect(navItemsFor(session).map((i) => i.path), contains('/scores'));
    });

    test('a guardian gets neither the marks tab nor a staff screen', () async {
      final session = await sessionFor(const []);
      final paths = navItemsFor(session).map((i) => i.path).toList();
      expect(paths, isNot(contains('/scores')));
      expect(session.can('people.view_staff'), isFalse);
    });
  });

  group('signing out on top of unsent work', () {
    setUp(() {
      TestWidgetsFlutterBinding.ensureInitialized();
      SharedPreferences.setMockInitialValues({});
    });

    Future<Session> sessionWithQueue(http.Client client) async {
      final store = OfflineStore(await SharedPreferences.getInstance());
      final session = Session(
        api: ApiClient(store: store, httpClient: client),
        store: store,
      );
      await session.api.post(
        '/attendance/records/',
        body: const {'present': true},
        queue: true,
        label: 'Register for JSS1A',
      );
      return session;
    }

    Future<bool?> tapSignOut(WidgetTester tester, Session session) async {
      bool? allowed;
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => TextButton(
              onPressed: () async =>
                  allowed = await confirmSignOut(context, session),
              child: const Text('Sign out'),
            ),
          ),
        ),
      );
      await tester.tap(find.text('Sign out'));
      await tester.pumpAndSettle();
      return allowed;
    }

    testWidgets('what is about to be lost is named, and can be kept', (
      tester,
    ) async {
      final session = await sessionWithQueue(
        MockClient((_) async => throw http.ClientException('down')),
      );

      await tapSignOut(tester, session);
      expect(find.text('Unsent changes'), findsOneWidget);
      expect(find.textContaining('1 change'), findsOneWidget);

      await tester.tap(find.text('Stay signed in'));
      await tester.pumpAndSettle();
      // Cancelling must leave the queue intact: the whole point is that the
      // register survives until it can be sent.
      expect(await session.api.pendingCount, 1);
    });

    testWidgets('a queue that flushes on the way out asks nothing', (
      tester,
    ) async {
      final session = await sessionWithQueue(
        MockClient((_) async => http.Response('{}', 200)),
      );

      expect(await tapSignOut(tester, session), isTrue);
      expect(find.text('Unsent changes'), findsNothing);
      expect(await session.api.pendingCount, 0);
    });
  });

  group('the students screen', () {
    setUp(() {
      TestWidgetsFlutterBinding.ensureInitialized();
      SharedPreferences.setMockInitialValues({});
    });

    /// The two-column layout puts the form in the right-hand pane, which shares
    /// its Scaffold with the "Add student" FAB. The FAB paints over the body's
    /// bottom-right — where the form's own save button sits — so without the
    /// clearance the registrar has no button to press and cannot save at all.
    testWidgets('the pane save button is not buried under the FAB', (
      tester,
    ) async {
      await tester.binding.setSurfaceSize(const Size(1280, 720));
      addTearDown(() => tester.binding.setSurfaceSize(null));

      final sent = <http.Request>[];
      final store = OfflineStore(await SharedPreferences.getInstance());
      final client = MockClient((request) async {
        if (request.method == 'POST') {
          sent.add(request);
          return http.Response('{"id":"new"}', 201);
        }
        if (request.url.path.endsWith('/arms/')) {
          return http.Response(
            '[{"id":"arm-gold","label":"SS2 Gold","level":"level-ss2",'
            '"capacity":40,"enrolled":1,"is_active":true}]',
            200,
          );
        }
        return http.Response('[]', 200);
      });
      final session =
          Session(
              api: ApiClient(store: store, httpClient: client),
              store: store,
            )
            ..user = {
              'full_name': 'Registrar',
              'permissions': ['people.manage_student'],
            };

      await tester.pumpWidget(
        MaterialApp(home: StudentsScreen(session: session)),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Add student'));
      await tester.pumpAndSettle();
      await tester.drag(
        find.byType(SingleChildScrollView).last,
        const Offset(0, -2000),
      );
      await tester.pumpAndSettle();

      final save = find.widgetWithText(FilledButton, 'Add');
      expect(
        tester.getRect(save).overlaps(
          tester.getRect(find.byType(FloatingActionButton)),
        ),
        isFalse,
      );

      await tester.enterText(
        find.widgetWithText(TextField, 'First name'),
        'Ada',
      );
      await tester.enterText(
        find.widgetWithText(TextField, 'Last name'),
        'Obi',
      );
      await tester.pumpAndSettle();
      await tester.tap(save);
      await tester.pumpAndSettle();

      expect(sent.single.url.path, endsWith('/people/students/'));
    });

    /// On a phone the form is a full-height bottom sheet, and a SnackBar is
    /// painted by the page's Scaffold underneath it — the refusal landed behind
    /// the sheet and the registrar saw a button that did nothing.
    testWidgets('a refusal from inside the sheet is actually readable', (
      tester,
    ) async {
      await tester.binding.setSurfaceSize(const Size(400, 800));
      addTearDown(() => tester.binding.setSurfaceSize(null));

      final store = OfflineStore(await SharedPreferences.getInstance());
      final client = MockClient((request) async {
        if (request.method == 'POST') {
          return http.Response(
            '{"error":{"code":"ARM_FULL","message":"SS2 Gold is full."}}',
            400,
          );
        }
        if (request.url.path.endsWith('/arms/')) {
          return http.Response(
            '[{"id":"arm-gold","label":"SS2 Gold","level":"level-ss2",'
            '"capacity":40,"enrolled":1,"is_active":true}]',
            200,
          );
        }
        return http.Response('[]', 200);
      });
      final session =
          Session(
              api: ApiClient(store: store, httpClient: client),
              store: store,
            )
            ..user = {
              'full_name': 'Registrar',
              'permissions': ['people.manage_student'],
            };

      await tester.pumpWidget(
        MaterialApp(home: StudentsScreen(session: session)),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Add student'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.widgetWithText(TextField, 'First name'),
        'Ada',
      );
      await tester.enterText(
        find.widgetWithText(TextField, 'Last name'),
        'Obi',
      );
      await tester.pumpAndSettle();
      await tester.drag(
        find.byType(SingleChildScrollView).last,
        const Offset(0, -2000),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Add'));
      await tester.pumpAndSettle();

      // Not merely present — on screen and on top of the sheet.
      expect(find.text('SS2 Gold is full.').hitTestable(), findsOneWidget);
      // And the form is still there to correct, not popped.
      await tester.tap(find.widgetWithText(TextButton, 'OK'));
      await tester.pumpAndSettle();
      expect(find.widgetWithText(TextField, 'First name'), findsOneWidget);
    });
  });

  group('showApiMessage', () {
    testWidgets('on a page it stays a snackbar', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Builder(
              builder: (context) => TextButton(
                onPressed: () => showApiMessage(context, 'Something went wrong'),
                child: const Text('go'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('go'));
      await tester.pumpAndSettle();

      expect(find.byType(SnackBar), findsOneWidget);
      expect(find.text('Something went wrong').hitTestable(), findsOneWidget);
    });
  });
}

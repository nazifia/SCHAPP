import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:schapp/src/design/theme.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// The button minimums are a theme constant, but they are load-bearing: an
/// infinite `minimumSize` width (what `Size.fromHeight` gives you) crashes any
/// button placed in a Row, because a Row hands non-flex children unbounded
/// width. That shipped once. These pin the shape of the fix, not the pixels.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  Widget host(Widget child, {Brightness brightness = Brightness.light}) =>
      MaterialApp(
        theme: schappTheme(brightness),
        home: Scaffold(body: child),
      );

  group('button minimums survive an unbounded parent', () {
    testWidgets('a FilledButton lays out inside a Row', (tester) async {
      await tester.pumpWidget(
        host(
          Row(
            children: [
              const Spacer(),
              TextButton(onPressed: () {}, child: const Text('Close')),
              FilledButton(onPressed: () {}, child: const Text('Save')),
            ],
          ),
        ),
      );

      expect(tester.takeException(), isNull);
      final button = tester.getSize(find.byType(FilledButton));
      expect(button.width, lessThan(800));
      expect(button.height, greaterThanOrEqualTo(52));
    });

    testWidgets('an ElevatedButton lays out inside a Row', (tester) async {
      await tester.pumpWidget(
        host(
          Row(
            children: [
              const Spacer(),
              ElevatedButton(onPressed: () {}, child: const Text('Save')),
            ],
          ),
        ),
      );

      expect(tester.takeException(), isNull);
      expect(tester.getSize(find.byType(ElevatedButton)).width, lessThan(800));
    });

    testWidgets('dark theme carries the same minimums', (tester) async {
      await tester.pumpWidget(
        host(
          Row(
            children: [
              const Spacer(),
              FilledButton(onPressed: () {}, child: const Text('Save')),
            ],
          ),
          brightness: Brightness.dark,
        ),
      );

      expect(tester.takeException(), isNull);
      expect(tester.getSize(find.byType(FilledButton)).height, 52);
    });
  });

  // The touch target is the reason the minimum exists at all: one-handed, at a
  // gate. Losing the 52 to fix the width would be trading one bug for another.
  testWidgets('a stretched button keeps the 52dp target', (tester) async {
    await tester.pumpWidget(
      host(
        const Padding(
          padding: EdgeInsets.all(12),
          child: SizedBox(
            width: double.infinity,
            child: FilledButton(onPressed: null, child: Text('Save register')),
          ),
        ),
      ),
    );

    expect(tester.takeException(), isNull);
    final size = tester.getSize(find.byType(FilledButton));
    expect(size.height, 52);
    expect(size.width, 800 - 24);
  });

  // The palette is data, but "readable" is a property that can be broken by a
  // one-character edit, and a register read outdoors has no margin. Every text
  // colour is checked against the two ends of the page gradient and against a
  // card sitting on them: WCAG AA, 4.5:1.
  group('text stays readable on the background', () {
    double contrast(Color fg, Color bg) {
      // Alpha is the whole point here — these colours are translucent.
      final a = Color.alphaBlend(fg, bg).computeLuminance() + 0.05;
      final b = bg.computeLuminance() + 0.05;
      return a > b ? a / b : b / a;
    }

    for (final brightness in Brightness.values) {
      testWidgets('${brightness.name}: AA against gradient and card', (
        tester,
      ) async {
        late BuildContext ctx;
        await tester.pumpWidget(
          host(
            Builder(
              builder: (context) {
                ctx = context;
                return const SizedBox();
              },
            ),
            brightness: brightness,
          ),
        );

        final gradient = ctx.bgGradient.gradient as LinearGradient;
        final surfaces = [
          ...gradient.colors,
          // The card is drawn over the gradient, so its own alpha matters.
          for (final base in gradient.colors)
            Color.alphaBlend(ctx.cardColor, base),
        ];

        for (final surface in surfaces) {
          for (final text in [
            ctx.labelColor,
            ctx.subLabelColor,
            ctx.hintColor,
          ]) {
            expect(
              contrast(text, surface),
              greaterThanOrEqualTo(4.5),
              reason: '$text on $surface',
            );
          }
        }
      });
    }
  });

  // The choice is only worth making if it survives the app being killed, and
  // a value written by a newer build must not brick the older one.
  group('ThemeController', () {
    Future<ThemeController> controller(Map<String, Object> stored) async {
      SharedPreferences.setMockInitialValues(stored);
      return ThemeController(await SharedPreferences.getInstance());
    }

    test('defaults to following the device', () async {
      final theme = await controller({});
      await theme.restore();
      expect(theme.value, ThemeMode.system);
    });

    test('restores the saved mode', () async {
      final theme = await controller({'schapp.theme_mode.v1': 'dark'});
      await theme.restore();
      expect(theme.value, ThemeMode.dark);
    });

    test('an unknown saved mode falls back to system', () async {
      final theme = await controller({'schapp.theme_mode.v1': 'sepia'});
      await theme.restore();
      expect(theme.value, ThemeMode.system);
    });

    test('set persists and notifies', () async {
      final theme = await controller({});
      var notified = 0;
      theme.addListener(() => notified++);

      await theme.set(ThemeMode.dark);
      expect(theme.value, ThemeMode.dark);
      expect(notified, 1);

      // Setting the same mode again is not a change.
      await theme.set(ThemeMode.dark);
      expect(notified, 1);

      final reopened = ThemeController(await SharedPreferences.getInstance());
      await reopened.restore();
      expect(reopened.value, ThemeMode.dark);
    });
  });
}

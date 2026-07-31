import 'package:flutter/material.dart';

import 'src/api/cache.dart';
import 'src/api/client.dart';
import 'src/auth/session.dart';
import 'src/design/theme.dart';
import 'src/router.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final store = OfflineStore();
  final api = ApiClient(store: store);
  final session = Session(api: api, store: store);
  final theme = ThemeController();
  // Tokens, the chosen school and the theme choice come off the device before
  // the first frame, so a returning user never sees the sign-in screen flash
  // past — or, in dark mode, a white one.
  await Future.wait([session.restore(), theme.restore()]);
  runApp(SchappApp(session: session, theme: theme));
}

class SchappApp extends StatefulWidget {
  const SchappApp({super.key, required this.session, required this.theme});

  final Session session;
  final ThemeController theme;

  @override
  State<SchappApp> createState() => _SchappAppState();
}

class _SchappAppState extends State<SchappApp> {
  late final _router = buildRouter(widget.session, widget.theme);
  AppLifecycleListener? _lifecycle;

  @override
  void initState() {
    super.initState();
    // Coming back to the foreground is the cheapest signal that the network may
    // be back: no connectivity plugin, and it covers the walk-out-of-the-
    // staffroom case a stream subscription is usually added for.
    _lifecycle = AppLifecycleListener(
      onResume: () => widget.session.api.flushOutbox(),
    );
  }

  @override
  void dispose() {
    _lifecycle?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([widget.session, widget.theme]),
      builder: (context, _) {
        final seed = widget.session.tenant?.primaryColor;
        return MaterialApp.router(
          title: 'SCHAPP',
          debugShowCheckedModeBanner: false,
          theme: schappTheme(Brightness.light, seedHex: seed),
          darkTheme: schappTheme(Brightness.dark, seedHex: seed),
          themeMode: widget.theme.value,
          routerConfig: _router,
          // Every Scaffold is transparent, so the page background is painted
          // once here instead of by each screen.
          builder: (context, child) => _Background(child: child!),
        );
      },
    );
  }
}

/// The gradient page plus the two out-of-focus orbs that give the surface some
/// depth. Purely decorative, so it is excluded from semantics and never
/// intercepts a tap.
class _Background extends StatelessWidget {
  const _Background({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return DecoratedBox(
      decoration: context.bgGradient,
      child: Stack(
        children: [
          ExcludeSemantics(
            child: IgnorePointer(
              child: Stack(
                children: [
                  Positioned(
                    top: -60,
                    right: -60,
                    child: _Orb(color: scheme.primary, size: 220),
                  ),
                  Positioned(
                    bottom: 100,
                    left: -80,
                    child: _Orb(color: AppColors.accentPurple, size: 200),
                  ),
                ],
              ),
            ),
          ),
          child,
        ],
      ),
    );
  }
}

class _Orb extends StatelessWidget {
  const _Orb({required this.color, required this.size});

  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) => Container(
    width: size,
    height: size,
    decoration: BoxDecoration(
      shape: BoxShape.circle,
      // Stronger than decorative: the glass surfaces blur what is behind them,
      // and a 6% orb blurs down to nothing at all.
      color: color.withValues(alpha: context.isDark ? 0.18 : 0.14),
    ),
  );
}

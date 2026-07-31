import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// The design system is Material 3 plus one decision: the seed colour comes
/// from the tenant's branding, so every school gets its own palette without a
/// second theme file. Schools send `#RRGGBB`; anything unparseable falls back.
///
/// The surface treatment — slate background, gradient page, frosted cards,
/// Outfit headings over Inter body — is shared with PharmApp so the two
/// products read as one family.
class AppColors {
  static const primaryDark = Color(0xFF0F172A); // Slate 900
  static const primaryTeal = Color(0xFF0D9488); // Teal 600
  static const accentCyan = Color(0xFF06B6D4);
  static const accentPurple = Color(0xFF8B5CF6);
  static const surfaceDark = Color(0xFF1E293B);
  static const backgroundLight = Color(0xFFF8FAFC);
  static const successGreen = Color(0xFF10B981);
  static const warningAmber = Color(0xFFF59E0B);
  static const errorRed = Color(0xFFEF4444);
  static const infoBlue = Color(0xFF3B82F6);
}

/// Kept: a school with no branding still gets the SCHAPP identity.
const schappGreen = Color(0xFF0B6E4F);

/// Light, dark, or follow the device.
///
/// ponytail: a [ValueNotifier] over one `shared_preferences` key, not a setting
/// on [Session] — the choice outlives sign-out, and the sign-in screens want it
/// too. Server-side sync is the upgrade path, if the same person ever wants the
/// same choice on a second device.
class ThemeController extends ValueNotifier<ThemeMode> {
  /// The instance is injectable so tests can hand in
  /// `SharedPreferences.setMockInitialValues` without a plugin.
  ThemeController([this._prefs]) : super(ThemeMode.system);

  static const _key = 'schapp.theme_mode.v1';

  SharedPreferences? _prefs;

  Future<SharedPreferences> get _p async =>
      _prefs ??= await SharedPreferences.getInstance();

  /// Read before the first frame, so a dark-mode user never sees a white flash.
  /// An unknown value — a mode written by a newer build — reads as `system`.
  Future<void> restore() async {
    final saved = (await _p).getString(_key);
    value = ThemeMode.values.firstWhere(
      (mode) => mode.name == saved,
      orElse: () => ThemeMode.system,
    );
  }

  Future<void> set(ThemeMode mode) async {
    if (mode == value) return;
    value = mode;
    await (await _p).setString(_key, mode.name);
  }
}

ThemeData schappTheme(Brightness brightness, {String? seedHex}) {
  final dark = brightness == Brightness.dark;
  final seed = colorFromHex(seedHex) ?? AppColors.primaryTeal;
  final scheme = ColorScheme.fromSeed(
    seedColor: seed,
    brightness: brightness,
    surface: dark ? AppColors.surfaceDark : AppColors.backgroundLight,
    surfaceTint: Colors.transparent,
  );
  final onBg = dark ? Colors.white : AppColors.primaryDark;

  return ThemeData(
    colorScheme: scheme,
    // The gradient is painted once, above the router, in main.dart. Every
    // Scaffold sits on top of it rather than each one repainting a background.
    scaffoldBackgroundColor: Colors.transparent,
    textTheme: TextTheme(
      displayLarge: GoogleFonts.outfit(
        fontWeight: FontWeight.bold,
        color: onBg,
      ),
      headlineSmall: GoogleFonts.outfit(
        fontWeight: FontWeight.w700,
        color: onBg,
      ),
      titleLarge: GoogleFonts.outfit(fontWeight: FontWeight.w600, color: onBg),
      titleMedium: GoogleFonts.outfit(fontWeight: FontWeight.w600, color: onBg),
      titleSmall: GoogleFonts.outfit(fontWeight: FontWeight.w600, color: onBg),
      bodyLarge: GoogleFonts.inter(color: dark ? Colors.white : Colors.black87),
      bodyMedium: GoogleFonts.inter(
        color: dark
            ? Colors.white.withValues(alpha: 0.86)
            : Colors.black.withValues(alpha: 0.72),
      ),
      labelLarge: GoogleFonts.inter(fontWeight: FontWeight.w500),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: dark ? Colors.white.withValues(alpha: 0.10) : Colors.white,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: BorderSide.none,
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: BorderSide(
          color: dark
              ? Colors.white.withValues(alpha: 0.22)
              : Colors.black.withValues(alpha: 0.14),
        ),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: BorderSide(color: scheme.primary, width: 2),
      ),
      contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 18),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        // 52dp targets, not 40: this is used one-handed, outdoors, at a gate.
        // Height only — an infinite min width blows up inside a Row.
        minimumSize: const Size(64, 52),
        elevation: 0,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        textStyle: GoogleFonts.inter(fontWeight: FontWeight.w600, fontSize: 16),
      ),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: scheme.primary,
        foregroundColor: scheme.onPrimary,
        elevation: 0,
        minimumSize: const Size(64, 52),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        textStyle: GoogleFonts.inter(fontWeight: FontWeight.w600, fontSize: 16),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: scheme.primary,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        textStyle: GoogleFonts.inter(fontWeight: FontWeight.w600),
      ),
    ),
    cardTheme: CardThemeData(
      // Matches SchappContext.cardColor / borderColor: a plain Card and a
      // GlassCard sit side by side on the same page and must not disagree.
      color: dark
          ? Colors.white.withValues(alpha: 0.14)
          : Colors.white.withValues(alpha: 0.94),
      elevation: 0,
      clipBehavior: Clip.antiAlias,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(20),
        side: BorderSide(
          color: dark
              ? Colors.white.withValues(alpha: 0.24)
              : Colors.black.withValues(alpha: 0.14),
        ),
      ),
      margin: const EdgeInsets.symmetric(vertical: 6),
    ),
    appBarTheme: AppBarTheme(
      backgroundColor: Colors.transparent,
      elevation: 0,
      scrolledUnderElevation: 0,
      titleTextStyle: GoogleFonts.outfit(
        color: onBg,
        fontSize: 20,
        fontWeight: FontWeight.w700,
      ),
      iconTheme: IconThemeData(color: onBg),
    ),
    navigationBarTheme: NavigationBarThemeData(
      // Translucent enough for the blur behind it to show, opaque enough that
      // a label on it is still readable over a scrolled list.
      backgroundColor: dark
          ? const Color(0xFF0B1220).withValues(alpha: 0.72)
          : Colors.white.withValues(alpha: 0.72),
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      indicatorColor: scheme.primary.withValues(alpha: 0.28),
      labelTextStyle: WidgetStatePropertyAll(
        GoogleFonts.inter(fontSize: 12, fontWeight: FontWeight.w500),
      ),
    ),
    navigationRailTheme: NavigationRailThemeData(
      backgroundColor: Colors.transparent,
      indicatorColor: scheme.primary.withValues(alpha: 0.28),
      selectedLabelTextStyle: GoogleFonts.inter(fontWeight: FontWeight.w600),
      unselectedLabelTextStyle: GoogleFonts.inter(),
    ),
    // 52dp targets, not 40: this is used one-handed, outdoors, at a gate.
    listTileTheme: const ListTileThemeData(minVerticalPadding: 12),
    chipTheme: ChipThemeData(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      side: BorderSide(
        color: dark
            ? Colors.white.withValues(alpha: 0.24)
            : Colors.black.withValues(alpha: 0.14),
      ),
    ),
    dividerTheme: DividerThemeData(
      color: dark
          ? Colors.white.withValues(alpha: 0.16)
          : Colors.black.withValues(alpha: 0.14),
      thickness: 1,
      space: 1,
    ),
    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: Colors.black.withValues(alpha: 0.85),
      actionTextColor: AppColors.accentCyan,
      contentTextStyle: GoogleFonts.inter(color: Colors.white, fontSize: 14),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: dark ? AppColors.surfaceDark : Colors.white,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
    ),
    bottomSheetTheme: BottomSheetThemeData(
      backgroundColor: dark ? AppColors.surfaceDark : Colors.white,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
    ),
  );
}

Color? colorFromHex(String? hex) {
  if (hex == null) return null;
  final cleaned = hex.replaceFirst('#', '').trim();
  if (cleaned.length != 6 && cleaned.length != 8) return null;
  final value = int.tryParse(cleaned, radix: 16);
  if (value == null) return null;
  return Color(cleaned.length == 6 ? 0xFF000000 | value : value);
}

/// The colours that depend on brightness but not on the tenant seed. An
/// extension, not a widget, so a one-off `Container` can reach them too.
extension SchappContext on BuildContext {
  bool get isDark => Theme.of(this).brightness == Brightness.dark;

  /// Full-page gradient, painted once above the router. The stops are pushed
  /// apart — near-black to slate, white to a real blue — so a frosted card has
  /// something to sit against instead of vanishing into a flat wash.
  BoxDecoration get bgGradient => BoxDecoration(
    gradient: LinearGradient(
      colors: isDark
          ? const [Color(0xFF05070F), Color(0xFF0B1220), Color(0xFF17233D)]
          : const [Color(0xFFFFFFFF), Color(0xFFE8EFFA), Color(0xFFCBE2F7)],
      begin: Alignment.topLeft,
      end: Alignment.bottomRight,
    ),
  );

  Color get cardColor => isDark
      ? Colors.white.withValues(alpha: 0.14)
      : Colors.white.withValues(alpha: 0.94);

  Color get borderColor => isDark
      ? Colors.white.withValues(alpha: 0.24)
      : Colors.black.withValues(alpha: 0.14);

  Color get labelColor => isDark ? Colors.white : Colors.black87;

  Color get subLabelColor => isDark
      ? Colors.white.withValues(alpha: 0.86)
      : Colors.black.withValues(alpha: 0.72);

  Color get hintColor => isDark
      ? Colors.white.withValues(alpha: 0.70)
      : Colors.black.withValues(alpha: 0.60);

  /// The specular sheen that makes a blurred surface read as glass rather than
  /// as a grey box: light catches the top-left edge, falls off, and comes back
  /// weaker at the bottom. Blended into the fill rather than painted over the
  /// child — a white veil on top of a mark sheet is a white veil over the marks.
  Gradient glassSheen(Color base) {
    Color lit(double alpha) =>
        Color.alphaBlend(Colors.white.withValues(alpha: alpha), base);
    return LinearGradient(
      begin: Alignment.topLeft,
      end: Alignment.bottomRight,
      colors: isDark
          ? [lit(0.16), base, lit(0.07)]
          : [lit(0.45), base, lit(0.20)],
      stops: const [0.0, 0.55, 1.0],
    );
  }
}

/// Frosted-glass card. `Card` already carries the same fill and radius from the
/// theme; this one adds the blur, the specular sheen and the lit rim, for the
/// surfaces that sit over the gradient and want it — nav bars, the offline
/// strip, hero panels.
class GlassCard extends StatelessWidget {
  const GlassCard({
    super.key,
    required this.child,
    this.padding,
    this.borderRadius = 20,
    this.blur = 20,
    this.color,
    this.onTap,
    this.sheen = true,
  });

  final Widget child;
  final EdgeInsetsGeometry? padding;
  final double borderRadius;
  final double blur;
  final Color? color;
  final VoidCallback? onTap;

  /// Off for a surface that already sits under content it would wash out.
  final bool sheen;

  @override
  Widget build(BuildContext context) {
    final radius = BorderRadius.circular(borderRadius);
    final fill = color ?? context.cardColor;
    Widget card = ClipRRect(
      borderRadius: radius,
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: blur, sigmaY: blur),
        child: Container(
          padding: padding,
          decoration: BoxDecoration(
            // Gradient or colour, never both: BoxDecoration ignores the colour
            // when a gradient is set, and the sheen already carries the fill.
            gradient: sheen ? context.glassSheen(fill) : null,
            color: sheen ? null : fill,
            borderRadius: radius,
            border: Border.all(color: context.borderColor),
          ),
          child: child,
        ),
      ),
    );
    if (onTap != null) {
      card = Material(
        color: Colors.transparent,
        child: InkWell(borderRadius: radius, onTap: onTap, child: card),
      );
    }
    return card;
  }
}

/// Small status pill: the one piece of PharmApp's badge set this app uses.
class StatusBadge extends StatelessWidget {
  const StatusBadge({
    super.key,
    required this.text,
    required this.icon,
    required this.color,
  });

  const StatusBadge.success(String text, {Key? key})
    : this(
        key: key,
        text: text,
        icon: Icons.check_circle,
        color: AppColors.successGreen,
      );

  const StatusBadge.warning(String text, {Key? key})
    : this(
        key: key,
        text: text,
        icon: Icons.warning_amber_rounded,
        color: AppColors.warningAmber,
      );

  const StatusBadge.error(String text, {Key? key})
    : this(key: key, text: text, icon: Icons.error, color: AppColors.errorRed);

  const StatusBadge.info(String text, {Key? key})
    : this(
        key: key,
        text: text,
        icon: Icons.info_outline,
        color: AppColors.infoBlue,
      );

  final String text;
  final IconData icon;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
    decoration: BoxDecoration(
      color: color.withValues(alpha: 0.10),
      borderRadius: BorderRadius.circular(12),
      border: Border.all(color: color.withValues(alpha: 0.30)),
    ),
    child: Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, color: color, size: 14),
        const SizedBox(width: 4),
        Text(
          text,
          style: TextStyle(
            color: color,
            fontSize: 12,
            fontWeight: FontWeight.w600,
          ),
        ),
      ],
    ),
  );
}

/// One place for the two breakpoints the layout actually changes at.
enum ScreenSize {
  /// Phone. Bottom navigation, one column.
  compact,

  /// Small tablet, split-screen. Navigation rail, one wide column.
  medium,

  /// Tablet landscape, desktop, web. Extended rail, two columns.
  expanded;

  static ScreenSize of(BuildContext context) {
    final width = MediaQuery.sizeOf(context).width;
    if (width < 600) return ScreenSize.compact;
    if (width < 1024) return ScreenSize.medium;
    return ScreenSize.expanded;
  }

  bool get isCompact => this == ScreenSize.compact;
}

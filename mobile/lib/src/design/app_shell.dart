import 'dart:ui';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../api/api_error.dart';
import '../api/client.dart';
import '../auth/session.dart';
import 'theme.dart';

/// How wide a page is ever allowed to get, regardless of the window. Lists and
/// registers are read left-to-right; past roughly this the eye loses the row.
const kContentMaxWidth = 1100.0;

/// How much room a screen needs before a list and the record it opens fit
/// side by side. Measured against the box the screen is actually handed —
/// the rail and the cap above have already come out of it — never the window.
const kTwoColumnFrom = 840.0;

/// The master column in a two-column screen. Wide enough for a name and a
/// number, narrow enough to leave the record the rest.
const kMasterWidth = 360.0;

class NavItem {
  const NavItem({required this.path, required this.icon, required this.label});

  final String path;
  final IconData icon;
  final String label;
}

/// The responsive frame every signed-in screen sits in: bottom bar on a phone,
/// rail on a tablet, extended rail on desktop and web. Also the single place
/// the offline state is announced, so no screen has to draw its own banner.
class AppShell extends StatelessWidget {
  const AppShell({
    super.key,
    required this.child,
    required this.items,
    required this.currentPath,
    required this.offline,
    required this.onSelect,
  });

  final Widget child;
  final List<NavItem> items;
  final String currentPath;
  final ValueListenable<bool> offline;
  final ValueChanged<String> onSelect;

  int get _index {
    final match = items.indexWhere(
      (i) => currentPath.startsWith(i.path) && i.path != '/',
    );
    if (match >= 0) return match;
    return items.indexWhere((i) => i.path == '/').clamp(0, items.length - 1);
  }

  @override
  Widget build(BuildContext context) {
    final size = ScreenSize.of(context);
    final body = Column(
      children: [
        _OfflineStrip(offline: offline),
        // Capped and centred once here, so no screen repeats it: a register
        // stretched across a 1600px browser window is one line of text
        // followed by a metre of nothing. The strip above stays full-bleed —
        // "you are offline" is a property of the window, not of the page.
        Expanded(
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: kContentMaxWidth),
              child: child,
            ),
          ),
        ),
      ],
    );

    if (size.isCompact) {
      return Scaffold(
        // Not `extendBody`: a translucent bar over a scrolling list looks good
        // and hides the last row, and this app's last row is a pupil's mark.
        body: body,
        bottomNavigationBar: ClipRect(
          child: BackdropFilter(
            filter: ImageFilter.blur(sigmaX: 24, sigmaY: 24),
            child: DecoratedBox(
              // The lit edge is what separates a glass bar from a grey one:
              // without it the blur just looks like a smudge of the list.
              decoration: BoxDecoration(
                border: Border(top: BorderSide(color: context.borderColor)),
              ),
              child: NavigationBar(
                selectedIndex: _index,
                onDestinationSelected: (i) => onSelect(items[i].path),
                destinations: [
                  for (final item in items)
                    NavigationDestination(
                      icon: Icon(item.icon),
                      label: item.label,
                    ),
                ],
              ),
            ),
          ),
        ),
      );
    }

    return Scaffold(
      body: Row(
        children: [
          NavigationRail(
            extended: size == ScreenSize.expanded,
            selectedIndex: _index,
            onDestinationSelected: (i) => onSelect(items[i].path),
            labelType: size == ScreenSize.expanded
                ? NavigationRailLabelType.none
                : NavigationRailLabelType.selected,
            destinations: [
              for (final item in items)
                NavigationRailDestination(
                  icon: Icon(item.icon),
                  label: Text(item.label),
                ),
            ],
          ),
          const VerticalDivider(width: 1),
          Expanded(child: body),
        ],
      ),
    );
  }
}

class _OfflineStrip extends StatelessWidget {
  const _OfflineStrip({required this.offline});

  final ValueListenable<bool> offline;

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<bool>(
      valueListenable: offline,
      builder: (context, isOffline, _) => AnimatedSize(
        duration: const Duration(milliseconds: 150),
        child: !isOffline
            ? const SizedBox.shrink()
            : SizedBox(
                width: double.infinity,
                child: GlassCard(
                  // Square: this is full-bleed, and a rounded full-bleed strip
                  // leaves two slivers of gradient at the top corners.
                  borderRadius: 0,
                  blur: 24,
                  color: AppColors.warningAmber.withValues(alpha: 0.22),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 10,
                  ),
                  child: const Row(
                    children: [
                      Icon(
                        Icons.cloud_off,
                        size: 18,
                        color: AppColors.warningAmber,
                      ),
                      Expanded(
                        child: Padding(
                          padding: EdgeInsets.only(left: 8),
                          child: Text(
                            'Offline — showing saved data. Changes will sync.',
                            style: TextStyle(
                              color: AppColors.warningAmber,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
      ),
    );
  }
}

/// Centred, width-capped content. On a 13" browser window a form stretched to
/// 1400px is unreadable, and every screen would otherwise repeat this.
class PageBody extends StatelessWidget {
  const PageBody({super.key, required this.child, this.maxWidth = 560});

  final Widget child;
  final double maxWidth;

  @override
  Widget build(BuildContext context) => Center(
    child: ConstrainedBox(
      constraints: BoxConstraints(maxWidth: maxWidth),
      child: Padding(padding: const EdgeInsets.all(24), child: child),
    ),
  );
}

/// Shows the API's message, which is already written for a user.
void showApiMessage(BuildContext context, String message) {
  ScaffoldMessenger.of(context)
    ..clearSnackBars()
    ..showSnackBar(SnackBar(content: Text(message)));
}

/// Confirm a delete, run it, and explain a refusal properly.
///
/// The server refuses a delete that would cascade with 409
/// `DEPENDENTS_EXIST` and a count per model. That list is the entire answer
/// to "why not", and a snackbar truncates it, so it gets a dialog of its own.
/// Returns true only when the row is actually gone.
Future<bool> confirmDelete(
  BuildContext context, {
  required String title,
  required String message,
  required Future<void> Function() onConfirm,
  String confirmLabel = 'Delete',
}) async {
  final confirmed = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(title),
      content: Text(message),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context, false),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, true),
          child: Text(confirmLabel),
        ),
      ],
    ),
  );
  if (confirmed != true) return false;

  try {
    await onConfirm();
    return true;
  } on ApiError catch (error) {
    if (!context.mounted) return false;
    if (error.dependents.isEmpty) {
      showApiMessage(context, error.message);
      return false;
    }
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Still in use'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(error.message),
            const SizedBox(height: 12),
            for (final entry in error.dependents.entries)
              Text('• ${entry.value} ${entry.key}'),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Close'),
          ),
        ],
      ),
    );
    return false;
  }
}

/// Signing out wipes the device, and the outbox goes with the cache — a
/// register marked in a dead zone would disappear without a word. So: one last
/// flush while the token is still valid, and if anything is left, say what will
/// be lost before it is.
///
/// The forced sign-outs — a revoked token family, a suspended school — cannot
/// ask, and their token is dead so the flush would fail anyway. Those still
/// lose the queue.
/// ponytail: the fix for that is keeping the outbox across sign-out keyed by
/// user id, which means school data outliving the session on a shared phone.
/// Worth it only once someone actually loses a register that way.
Future<bool> confirmSignOut(BuildContext context, Session session) async {
  await session.api.flushOutbox();
  final pending = await session.api.pendingCount;
  if (pending == 0) return true;
  if (!context.mounted) return false;

  final plural = pending == 1 ? '' : 's';
  final confirmed = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('Unsent changes'),
      content: Text(
        '$pending change$plural could not be sent and will be lost on sign '
        'out. Get back online and sync from the dashboard to keep '
        '${pending == 1 ? 'it' : 'them'}.',
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context, false),
          child: const Text('Stay signed in'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, true),
          child: const Text('Sign out anyway'),
        ),
      ],
    ),
  );
  return confirmed ?? false;
}

/// Convenience so screens can react to a flush without importing the client.
Future<void> syncPending(BuildContext context, ApiClient api) async {
  final result = await api.flushOutbox();
  if (!context.mounted || result.sent + result.rejected == 0) return;
  showApiMessage(
    context,
    result.rejected == 0
        ? '${result.sent} pending change${result.sent == 1 ? '' : 's'} synced.'
        : '${result.sent} synced, ${result.rejected} rejected by the server.',
  );
}

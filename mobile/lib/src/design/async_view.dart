import 'package:flutter/material.dart';

import '../api/api_error.dart';

/// Load once, show a spinner, show the API's own message on failure, and
/// pull-to-refresh. Every feature screen needs exactly this and none of them
/// should write it again.
///
/// The error state always offers a retry, because on this network the usual
/// cause is a lift, a tunnel or a NIN queue — not a bug.
class AsyncView<T> extends StatefulWidget {
  const AsyncView({
    super.key,
    required this.load,
    required this.builder,
    this.empty,
  });

  final Future<T> Function() load;
  final Widget Function(BuildContext context, T data) builder;

  /// Shown instead of [builder] when the result is an empty list.
  final Widget? empty;

  @override
  State<AsyncView<T>> createState() => AsyncViewState<T>();
}

class AsyncViewState<T> extends State<AsyncView<T>> {
  late Future<T> _future = widget.load();

  /// Screens call this after a write, so a submit does not need its own
  /// setState dance to show the new server state.
  void reload() {
    // Block body, not an arrow: an arrow returns the assigned Future and
    // setState rejects a callback that returns one.
    setState(() {
      _future = widget.load();
    });
  }

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: () async {
        reload();
        await _future.catchError((_) => null as T);
      },
      child: FutureBuilder<T>(
        future: _future,
        builder: (context, snapshot) {
          if (snapshot.connectionState == ConnectionState.waiting) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snapshot.hasError) {
            return _Failure(error: snapshot.error!, onRetry: reload);
          }
          final data = snapshot.data as T;
          if (data is List && data.isEmpty && widget.empty != null) {
            // Still scrollable, or pull-to-refresh cannot rescue an empty list.
            return ListView(children: [widget.empty!]);
          }
          return widget.builder(context, data);
        },
      ),
    );
  }
}

class _Failure extends StatelessWidget {
  const _Failure({required this.error, required this.onRetry});

  final Object error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final apiError = error is ApiError ? error as ApiError : null;
    return ListView(
      padding: const EdgeInsets.all(32),
      children: [
        Icon(
          apiError?.isOffline ?? false ? Icons.cloud_off : Icons.error_outline,
          size: 40,
          color: Theme.of(context).colorScheme.outline,
        ),
        const SizedBox(height: 12),
        Text(
          apiError?.message ?? 'Something went wrong.',
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.bodyLarge,
        ),
        const SizedBox(height: 16),
        Center(
          child: OutlinedButton.icon(
            onPressed: onRetry,
            icon: const Icon(Icons.refresh),
            label: const Text('Try again'),
          ),
        ),
      ],
    );
  }
}

/// The "nothing here" state, used by every list screen.
class EmptyState extends StatelessWidget {
  const EmptyState({super.key, required this.icon, required this.message});

  final IconData icon;
  final String message;

  @override
  Widget build(BuildContext context) {
    final content = Padding(
      padding: const EdgeInsets.all(40),
      child: Column(
        children: [
          Icon(icon, size: 40, color: Theme.of(context).colorScheme.outline),
          const SizedBox(height: 12),
          Text(
            message,
            textAlign: TextAlign.center,
            style: Theme.of(context).textTheme.bodyMedium,
          ),
        ],
      ),
    );
    // Half these are handed an `Expanded` — 130dp of it on a phone held
    // sideways, which the icon and two lines of copy overflowed. The other half
    // sit inside a ListView, where the height is unbounded and a viewport in a
    // viewport is an error, so the scroll only goes on when there is a box to
    // scroll inside.
    return LayoutBuilder(
      builder: (context, box) => box.hasBoundedHeight
          ? SingleChildScrollView(child: content)
          : content,
    );
  }
}

/// A term as a person names it. "First Term" on its own names one term a year,
/// so any picker listing more than one session's worth is a column of
/// identical rows — pick the wrong one and a whole screen is quietly about
/// last year. Pickers already scoped to one session pass the name alone.
///
/// `session_name` is the server's, so a term serialised without it — an older
/// build answering a newer app — reads as the bare name rather than a dangling
/// separator.
String termLabel(Map<String, dynamic> term) {
  final name = term['name'] as String? ?? '';
  final session = term['session_name'] as String? ?? '';
  return session.isEmpty ? name : '$name · $session';
}

/// ₦ with thousands separators. The app shows money on six screens and Nigeria
/// writes it one way; a package for one function would be silly.
String naira(Object? amount) {
  final value = double.tryParse('$amount') ?? 0;
  final fixed = value.toStringAsFixed(2);
  final parts = fixed.split('.');
  final digits = parts[0].replaceAllMapped(
    RegExp(r'(\d)(?=(\d{3})+$)'),
    (m) => '${m[1]},',
  );
  return '₦$digits.${parts[1]}';
}

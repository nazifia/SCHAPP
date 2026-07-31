import 'package:flutter/material.dart';
import 'package:printing/printing.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/async_view.dart';

/// ID cards for a class, as one PDF.
///
/// Nobody prints one card, so the class is the unit here. The PDF goes
/// straight to the OS sheet rather than being saved anywhere by the app: from
/// there it prints, or it goes to whoever owns the card printer. Each page
/// carries its own signed QR, so a batch is not a batch of interchangeable
/// cards.
class IdCardsScreen extends StatelessWidget {
  const IdCardsScreen({super.key, required this.session});

  final Session session;

  @override
  Widget build(BuildContext context) {
    final repo = Repository(session.api);
    return Scaffold(
      appBar: AppBar(title: const Text('ID cards')),
      body: AsyncView<List<Map<String, dynamic>>>(
        load: repo.classArms,
        empty: const EmptyState(
          icon: Icons.badge_outlined,
          message: 'No classes to print cards for yet.',
        ),
        builder: (context, arms) => _Batch(repo: repo, arms: arms),
      ),
    );
  }
}

class _Batch extends StatefulWidget {
  const _Batch({required this.repo, required this.arms});

  final Repository repo;
  final List<Map<String, dynamic>> arms;

  @override
  State<_Batch> createState() => _BatchState();
}

class _BatchState extends State<_Batch> {
  String? _arm;
  bool _busy = false;
  String? _failure;

  Future<void> _print() async {
    setState(() {
      _busy = true;
      _failure = null;
    });
    try {
      final pdf = await widget.repo.idCards(classArm: _arm);
      await Printing.sharePdf(bytes: pdf, filename: 'id-cards.pdf');
    } on ApiError catch (error) {
      if (mounted) setState(() => _failure = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(16),
    children: [
      Text(
        'One card per page, ready to print. A batch is capped at 400 cards — '
        'pick a class rather than the whole school.',
        style: Theme.of(context).textTheme.bodyMedium,
      ),
      const SizedBox(height: 16),
      DropdownButtonFormField<String?>(
        initialValue: _arm,
        isExpanded: true,
        decoration: const InputDecoration(
          labelText: 'Class',
          border: OutlineInputBorder(),
        ),
        items: [
          const DropdownMenuItem(
            value: null,
            child: Text('Everyone I can see'),
          ),
          for (final arm in widget.arms)
            DropdownMenuItem(
              value: arm['id'] as String?,
              // `label` is the serializer's "SS2 Gold" — the name alone
              // repeats across levels and a printer needs the right one.
              child: Text((arm['label'] ?? arm['name'] ?? '').toString()),
            ),
        ],
        onChanged: _busy ? null : (value) => setState(() => _arm = value),
      ),
      const SizedBox(height: 16),
      FilledButton.icon(
        onPressed: _busy ? null : _print,
        icon: _busy
            ? const SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : const Icon(Icons.print_outlined),
        label: Text(_busy ? 'Building the PDF…' : 'Print cards'),
      ),
      if (_failure != null) ...[
        const SizedBox(height: 16),
        Card(
          color: Theme.of(context).colorScheme.errorContainer,
          child: ListTile(
            leading: const Icon(Icons.error_outline),
            title: Text(_failure!),
          ),
        ),
      ],
    ],
  );
}

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../api/api_error.dart';
import '../design/app_shell.dart';
import 'session.dart';

/// "Find your school". The slug is what the API resolves a tenant from, and it
/// has to be entered before anything else can be asked for — every other route
/// needs the `X-Tenant-Slug` header.
class SchoolScreen extends StatefulWidget {
  const SchoolScreen({super.key, required this.session});

  final Session session;

  @override
  State<SchoolScreen> createState() => _SchoolScreenState();
}

class _SchoolScreenState extends State<SchoolScreen> {
  final _controller = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final code = _controller.text.trim();
    if (code.isEmpty) {
      setState(() => _error = 'Enter your school code.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.session.chooseSchool(code);
      if (mounted) context.go('/signin');
    } on ApiError catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Scaffold(
      body: PageBody(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Icon(
              Icons.school_outlined,
              size: 64,
              color: Theme.of(context).colorScheme.primary,
            ),
            const SizedBox(height: 24),
            Text(
              'SCHAPP',
              style: text.headlineMedium,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            Text(
              'Enter the code your school gave you.',
              style: text.bodyMedium,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 32),
            TextField(
              controller: _controller,
              autofocus: true,
              textInputAction: TextInputAction.go,
              autocorrect: false,
              onSubmitted: (_) => _submit(),
              decoration: InputDecoration(
                labelText: 'School code',
                hintText: 'kings-college',
                errorText: _error,
                prefixIcon: const Icon(Icons.tag),
              ),
            ),
            const SizedBox(height: 16),
            FilledButton(
              onPressed: _busy ? null : _submit,
              child: _busy
                  ? const SizedBox.square(
                      dimension: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('Continue'),
            ),
          ],
        ),
      ),
    );
  }
}

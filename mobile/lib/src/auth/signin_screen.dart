import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

import '../api/api_error.dart';
import '../design/app_shell.dart';
import '../phone.dart';
import 'session.dart';

/// Phone number, then either a code by SMS or the PIN on a known device.
///
/// The PIN path is offered unconditionally: the API answers a single
/// `PIN_INVALID` for an unknown number, an unset PIN, a wrong PIN and an
/// unrecognised device, and its message already tells the user to use a code
/// instead. Deciding locally whether to show the option would leak which.
class SignInScreen extends StatefulWidget {
  const SignInScreen({super.key, required this.session});

  final Session session;

  @override
  State<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends State<SignInScreen> {
  final _phone = TextEditingController();
  final _pin = TextEditingController();
  bool _usePin = false;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _phone.dispose();
    _pin.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final shapeError = phoneErrorFor(_phone.text);
    if (shapeError != null) {
      setState(() => _error = shapeError);
      return;
    }
    if (_usePin && _pin.text.length != 6) {
      setState(() => _error = 'Enter your 6-digit PIN.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      if (_usePin) {
        await widget.session.pinLogin(_phone.text, _pin.text);
        if (mounted) context.go('/');
      } else {
        final challenge = await widget.session.requestOtp(_phone.text);
        if (mounted) context.go('/signin/otp', extra: challenge);
      }
    } on ApiError catch (e) {
      if (e.requiresSchool && mounted) {
        await widget.session.forgetSchool();
        return;
      }
      setState(() => _error = e.message);
    } on InvalidMsisdn catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final tenant = widget.session.tenant;
    final text = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(
        title: Text(tenant?.name ?? 'Sign in'),
        actions: [
          TextButton(
            onPressed: widget.session.forgetSchool,
            child: const Text('Change school'),
          ),
        ],
      ),
      body: PageBody(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (tenant?.motto != null && tenant!.motto!.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 24),
                child: Text(
                  tenant.motto!,
                  style: text.titleMedium,
                  textAlign: TextAlign.center,
                ),
              ),
            TextField(
              controller: _phone,
              autofocus: true,
              keyboardType: TextInputType.phone,
              inputFormatters: [LengthLimitingTextInputFormatter(20)],
              decoration: const InputDecoration(
                labelText: 'Phone number',
                hintText: '0803 123 4567',
                prefixIcon: Icon(Icons.phone_outlined),
              ),
            ),
            if (_usePin) ...[
              const SizedBox(height: 16),
              TextField(
                controller: _pin,
                obscureText: true,
                keyboardType: TextInputType.number,
                maxLength: 6,
                inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                onSubmitted: (_) => _submit(),
                decoration: const InputDecoration(
                  labelText: '6-digit PIN',
                  prefixIcon: Icon(Icons.lock_outline),
                  counterText: '',
                ),
              ),
            ],
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(
                  _error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
            const SizedBox(height: 24),
            FilledButton(
              onPressed: _busy ? null : _submit,
              child: _busy
                  ? const SizedBox.square(
                      dimension: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : Text(_usePin ? 'Sign in' : 'Send me a code'),
            ),
            const SizedBox(height: 8),
            TextButton(
              onPressed: _busy
                  ? null
                  : () => setState(() {
                      _usePin = !_usePin;
                      _error = null;
                    }),
              child: Text(
                _usePin ? 'Use a code by SMS instead' : 'Use my PIN instead',
              ),
            ),
          ],
        ),
      ),
    );
  }
}

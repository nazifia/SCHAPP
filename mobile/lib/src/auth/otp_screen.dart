import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

import '../api/api_error.dart';
import '../design/app_shell.dart';
import 'session.dart';

/// Enter the 6-digit code.
///
/// One timer drives both countdowns: resend backs off server-side
/// (60s, 120s, 240s… capped at 15 minutes) and every rate-limited answer
/// carries the wait in `Retry-After`, so the button follows the server rather
/// than guessing.
class OtpScreen extends StatefulWidget {
  const OtpScreen({super.key, required this.session, required this.challenge});

  final Session session;
  final OtpChallenge challenge;

  @override
  State<OtpScreen> createState() => _OtpScreenState();
}

class _OtpScreenState extends State<OtpScreen> {
  final _code = TextEditingController();
  late OtpChallenge _challenge;
  late Timer _ticker;
  int _resendIn = 0;
  int _expiresIn = 0;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _challenge = widget.challenge;
    _resetCountdowns();
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) return;
      setState(() {
        if (_resendIn > 0) _resendIn--;
        if (_expiresIn > 0) _expiresIn--;
      });
    });
  }

  void _resetCountdowns() {
    _resendIn = _challenge.resendAfter.inSeconds;
    _expiresIn = _challenge.expiresIn.inSeconds;
  }

  @override
  void dispose() {
    _ticker.cancel();
    _code.dispose();
    super.dispose();
  }

  Future<void> _verify() async {
    if (_code.text.length != 6) {
      setState(() => _error = 'Enter the 6-digit code.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.session.verifyOtp(_challenge.requestId, _code.text);
      if (mounted) context.go('/');
    } on ApiError catch (e) {
      setState(() {
        _error = e.message;
        if (e.retryAfter != null) _resendIn = e.retryAfter!.inSeconds;
        // A spent or expired code cannot be retyped into working.
        if (const {'OTP_EXPIRED', 'OTP_ALREADY_USED'}.contains(e.code)) {
          _expiresIn = 0;
          _code.clear();
        }
      });
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _resend() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      _challenge = await widget.session.requestOtp(_challenge.phone);
      _code.clear();
      if (mounted) {
        setState(_resetCountdowns);
        showApiMessage(context, 'New code sent to ${_challenge.maskedPhone}.');
      }
    } on ApiError catch (e) {
      setState(() {
        _error = e.message;
        if (e.retryAfter != null) _resendIn = e.retryAfter!.inSeconds;
      });
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String get _resendLabel {
    if (_resendIn <= 0) return 'Send a new code';
    final minutes = _resendIn ~/ 60;
    final seconds = _resendIn % 60;
    return minutes > 0
        ? 'Send a new code in $minutes:${seconds.toString().padLeft(2, '0')}'
        : 'Send a new code in ${seconds}s';
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(
        leading: BackButton(onPressed: () => context.go('/signin')),
        title: const Text('Enter code'),
      ),
      body: PageBody(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              'We sent a 6-digit code to ${_challenge.maskedPhone}.',
              style: text.bodyLarge,
            ),
            const SizedBox(height: 4),
            Text(
              _expiresIn > 0
                  ? 'It expires in ${(_expiresIn ~/ 60)}:${(_expiresIn % 60).toString().padLeft(2, '0')}.'
                  : 'That code has expired — send a new one.',
              style: text.bodySmall,
            ),
            const SizedBox(height: 24),
            TextField(
              controller: _code,
              autofocus: true,
              keyboardType: TextInputType.number,
              maxLength: 6,
              textAlign: TextAlign.center,
              style: text.headlineMedium?.copyWith(letterSpacing: 8),
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              onChanged: (value) {
                if (value.length == 6 && !_busy) _verify();
              },
              decoration: const InputDecoration(
                counterText: '',
                hintText: '••••••',
              ),
            ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  _error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
            const SizedBox(height: 16),
            FilledButton(
              onPressed: _busy ? null : _verify,
              child: _busy
                  ? const SizedBox.square(
                      dimension: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('Verify'),
            ),
            const SizedBox(height: 8),
            TextButton(
              onPressed: (_busy || _resendIn > 0) ? null : _resend,
              child: Text(_resendLabel),
            ),
          ],
        ),
      ),
    );
  }
}

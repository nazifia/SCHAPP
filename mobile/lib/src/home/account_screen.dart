import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api/api_error.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/theme.dart';

/// Profile, PIN, signed-in devices, sign out.
///
/// Revoking a device kills its refresh family at once, but an access token
/// already in its hands stays valid for up to 15 more minutes — the copy says
/// so rather than promising an instant cut-off.
class AccountScreen extends StatefulWidget {
  const AccountScreen({super.key, required this.session, required this.theme});

  final Session session;
  final ThemeController theme;

  @override
  State<AccountScreen> createState() => _AccountScreenState();
}

class _AccountScreenState extends State<AccountScreen> {
  List<Map<String, dynamic>> _devices = const [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final devices = await widget.session.devices();
      if (mounted) setState(() => _devices = devices);
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _revoke(Map<String, dynamic> device) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Sign this device out?'),
        content: Text(
          '${device['name'] ?? device['platform'] ?? 'This device'} will have to '
          'sign in again. Its current session ends within 15 minutes.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Sign out device'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await widget.session.revokeDevice(device['id'] as String);
      await _load();
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    }
  }

  Future<void> _setPin() async {
    final controller = TextEditingController();
    final pin = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Set a 6-digit PIN'),
        content: TextField(
          controller: controller,
          autofocus: true,
          obscureText: true,
          keyboardType: TextInputType.number,
          maxLength: 6,
          inputFormatters: [FilteringTextInputFormatter.digitsOnly],
          decoration: const InputDecoration(labelText: 'PIN', counterText: ''),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (pin == null || pin.length != 6) return;
    try {
      await widget.session.setPin(pin);
      if (mounted) {
        showApiMessage(context, 'PIN set. It works on this device only.');
      }
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = widget.session;
    final user = session.user ?? const {};
    return Scaffold(
      appBar: AppBar(title: const Text('Account')),
      body: ListView(
        children: [
          ListTile(
            leading: const Icon(Icons.person_outline),
            title: Text(session.fullName),
            subtitle: Text(user['phone_display'] as String? ?? ''),
          ),
          ListTile(
            leading: const Icon(Icons.school_outlined),
            title: Text(session.tenant?.name ?? ''),
            subtitle: Text(session.tenant?.slug ?? ''),
          ),
          if ((user['nin'] as String?)?.isNotEmpty ?? false)
            ListTile(
              leading: const Icon(Icons.badge_outlined),
              title: const Text('NIN'),
              subtitle: Text(user['nin'] as String),
            ),
          const Divider(),
          ListTile(
            leading: const Icon(Icons.lock_outline),
            title: Text(session.hasPin ? 'Change PIN' : 'Set a PIN'),
            subtitle: const Text('Faster sign-in on this device only'),
            onTap: _setPin,
          ),
          const Divider(),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Text(
              'Appearance',
              style: Theme.of(context).textTheme.titleMedium,
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
            child: ValueListenableBuilder(
              valueListenable: widget.theme,
              builder: (context, mode, _) => SegmentedButton<ThemeMode>(
                // The checkmark would push a third segment off a narrow phone.
                showSelectedIcon: false,
                segments: const [
                  ButtonSegment(
                    value: ThemeMode.system,
                    icon: Icon(Icons.brightness_auto_outlined),
                    label: Text('Auto'),
                  ),
                  ButtonSegment(
                    value: ThemeMode.light,
                    icon: Icon(Icons.light_mode_outlined),
                    label: Text('Light'),
                  ),
                  ButtonSegment(
                    value: ThemeMode.dark,
                    icon: Icon(Icons.dark_mode_outlined),
                    label: Text('Dark'),
                  ),
                ],
                selected: {mode},
                onSelectionChanged: (selection) =>
                    widget.theme.set(selection.first),
              ),
            ),
          ),
          const Divider(),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Text(
              'Signed-in devices',
              style: Theme.of(context).textTheme.titleMedium,
            ),
          ),
          if (_loading)
            const Padding(
              padding: EdgeInsets.all(16),
              child: Center(child: CircularProgressIndicator()),
            )
          else
            for (final device in _devices.where((d) => d['revoked_at'] == null))
              ListTile(
                leading: Icon(
                  device['platform'] == 'WEB'
                      ? Icons.language
                      : Icons.phone_android,
                ),
                title: Text(
                  device['name'] as String? ??
                      device['platform'] as String? ??
                      'Device',
                ),
                subtitle: Text(
                  device['is_current'] == true
                      ? 'This device'
                      : 'Last seen ${device['last_seen_at'] ?? 'unknown'}',
                ),
                trailing: device['is_current'] == true
                    ? null
                    : IconButton(
                        icon: const Icon(Icons.logout),
                        tooltip: 'Sign out device',
                        onPressed: () => _revoke(device),
                      ),
              ),
          const Divider(),
          ListTile(
            leading: const Icon(Icons.swap_horiz),
            title: const Text('Change school'),
            subtitle: const Text(
              'Signs out and clears data saved on this device',
            ),
            onTap: () async {
              if (!await confirmSignOut(context, session)) return;
              await session.signOut();
              await session.forgetSchool();
            },
          ),
          ListTile(
            leading: const Icon(Icons.logout),
            title: const Text('Sign out'),
            onTap: () async {
              if (!await confirmSignOut(context, session)) return;
              await session.signOut();
            },
          ),
          const SizedBox(height: 24),
        ],
      ),
    );
  }
}

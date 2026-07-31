import 'package:flutter/material.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';
import '../phone.dart';

/// Staff records and the logins that hang off them.
///
/// Two tabs because they are two different jobs held by two different people:
/// HR keeps the personnel file (`people.manage_staff`), and whoever holds
/// `admin.manage_users` decides who may sign in and with what powers. A staff
/// row with no account is a teacher who cannot open the app at all, so the
/// list says which ones those are rather than making anyone guess.
class PeopleAdminScreen extends StatelessWidget {
  const PeopleAdminScreen({super.key, required this.session});

  final Session session;

  @override
  Widget build(BuildContext context) {
    final canManageUsers = session.can('admin.manage_users');
    return DefaultTabController(
      length: canManageUsers ? 2 : 1,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('Staff & access'),
          bottom: TabBar(
            tabs: [
              const Tab(text: 'Staff'),
              if (canManageUsers) const Tab(text: 'Accounts'),
            ],
          ),
        ),
        body: TabBarView(
          children: [
            _StaffTab(session: session),
            if (canManageUsers) _AccountsTab(session: session),
          ],
        ),
      ),
    );
  }
}

/// The search box every list on this screen shares. Server-side, because the
/// office has three hundred staff and the client holds one page of them.
class _SearchField extends StatelessWidget {
  const _SearchField({required this.hint, required this.onSubmitted});

  final String hint;
  final ValueChanged<String> onSubmitted;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
    child: TextField(
      decoration: InputDecoration(
        hintText: hint,
        prefixIcon: const Icon(Icons.search),
        isDense: true,
        border: const OutlineInputBorder(),
      ),
      textInputAction: TextInputAction.search,
      // On submit, not on every keystroke: each search is a round trip and
      // this network is not the one to spend eight of them on.
      onSubmitted: onSubmitted,
    ),
  );
}

// ---------------------------------------------------------------------------
// Staff
// ---------------------------------------------------------------------------

const _staffStatuses = {
  'ACTIVE': 'Active',
  'ON_LEAVE': 'On leave',
  'SUSPENDED': 'Suspended',
  'EXITED': 'Exited',
};

class _StaffTab extends StatefulWidget {
  const _StaffTab({required this.session});

  final Session session;

  @override
  State<_StaffTab> createState() => _StaffTabState();
}

class _StaffTabState extends State<_StaffTab> {
  final _key = GlobalKey<AsyncViewState<List<Map<String, dynamic>>>>();
  late final Repository _repo = Repository(widget.session.api);
  String _search = '';
  String? _status = 'ACTIVE';

  void _reload() => _key.currentState?.reload();

  Future<void> _add() async {
    final created = await showDialog<bool>(
      context: context,
      builder: (context) => _NewStaffDialog(repo: _repo),
    );
    if (created == true) _reload();
  }

  Future<void> _open(Map<String, dynamic> row) async {
    final changed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      // No width override: Material 3 already caps the sheet at 640 and
      // centres it, which is what a form wants on a tablet or a browser.
      builder: (context) => _StaffSheet(
        session: widget.session,
        repo: _repo,
        staffId: row['id'] as String,
      ),
    );
    if (changed == true) _reload();
  }

  @override
  Widget build(BuildContext context) {
    final canManage = widget.session.can('people.manage_staff');
    return Scaffold(
      floatingActionButton: canManage
          ? FloatingActionButton.extended(
              onPressed: _add,
              icon: const Icon(Icons.person_add_alt),
              label: const Text('Add staff'),
            )
          : null,
      body: Column(
        children: [
          _SearchField(
            hint: 'Name or staff number',
            onSubmitted: (value) {
              setState(() => _search = value.trim());
              _reload();
            },
          ),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Row(
              children: [
                for (final entry in {'': 'All', ..._staffStatuses}.entries)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 4),
                    child: ChoiceChip(
                      label: Text(entry.value),
                      selected: (_status ?? '') == entry.key,
                      onSelected: (_) {
                        setState(
                          () => _status = entry.key.isEmpty ? null : entry.key,
                        );
                        _reload();
                      },
                    ),
                  ),
              ],
            ),
          ),
          Expanded(
            child: AsyncView<List<Map<String, dynamic>>>(
              key: _key,
              load: () => _repo.staff(
                status: _status,
                search: _search.isEmpty ? null : _search,
              ),
              empty: const EmptyState(
                icon: Icons.badge_outlined,
                message: 'No staff match this filter.',
              ),
              builder: (context, staff) => ListView.separated(
                itemCount: staff.length,
                separatorBuilder: (_, _) => const Divider(height: 1),
                itemBuilder: (context, index) {
                  final row = staff[index];
                  final hasAccount = row['has_account'] == true;
                  return ListTile(
                    leading: CircleAvatar(
                      child: Text(_initials(row['full_name'] as String? ?? '')),
                    ),
                    title: Text(row['full_name'] as String? ?? ''),
                    subtitle: Text(
                      [
                        row['staff_number'],
                        if ((row['designation'] as String? ?? '').isNotEmpty)
                          row['designation'],
                        _staffStatuses[row['status']] ?? row['status'],
                      ].join(' · '),
                    ),
                    trailing: Icon(
                      hasAccount ? Icons.smartphone : Icons.no_cell_outlined,
                      // A missing login is the actionable state, so it is the
                      // one that is coloured.
                      color: hasAccount
                          ? Theme.of(context).colorScheme.primary
                          : Theme.of(context).colorScheme.outline,
                    ),
                    onTap: () => _open(row),
                  );
                },
              ),
            ),
          ),
        ],
      ),
    );
  }
}

String _initials(String name) {
  final parts = name.split(' ').where((p) => p.isNotEmpty).toList();
  if (parts.isEmpty) return '?';
  return parts.length == 1
      ? parts.first[0].toUpperCase()
      : '${parts.first[0]}${parts.last[0]}'.toUpperCase();
}

class _StaffSheet extends StatefulWidget {
  const _StaffSheet({
    required this.session,
    required this.repo,
    required this.staffId,
  });

  final Session session;
  final Repository repo;
  final String staffId;

  @override
  State<_StaffSheet> createState() => _StaffSheetState();
}

class _StaffSheetState extends State<_StaffSheet> {
  Map<String, dynamic>? _staff;
  bool _busy = false;
  bool _changed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final staff = await widget.repo.staffMember(widget.staffId);
      if (mounted) setState(() => _staff = staff);
    } on ApiError catch (e) {
      if (mounted) {
        showApiMessage(context, e.message);
        Navigator.pop(context, _changed);
      }
    }
  }

  Future<void> _run(Future<Map<String, dynamic>> Function() action) async {
    setState(() => _busy = true);
    try {
      final updated = await action();
      if (mounted) setState(() => _staff = updated);
      _changed = true;
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _setStatus(String status) async {
    if (status == 'EXITED') {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Record this exit?'),
          content: const Text(
            'Their login is closed and every phone they are signed in on is '
            'signed out. The personnel record is kept.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Record exit'),
            ),
          ],
        ),
      );
      if (confirmed != true) return;
    }
    await _run(
      () => widget.repo.updateStaff(widget.staffId, {'status': status}),
    );
  }

  /// Soft on the server: the file leaves the list, the audit rows and uploads
  /// that point at it survive. Recording an exit is the right move for someone
  /// who has left — it closes their login too — so this is for the duplicate
  /// and the record created in error.
  Future<void> _delete() async {
    final deleted = await confirmDelete(
      context,
      title: 'Remove this record?',
      message:
          '${_staff?['full_name'] ?? 'This record'} comes off the staff list. '
          'What they signed, uploaded or approved is kept.\n\n'
          'If they have left the school, set their employment to Exited '
          'instead — that also closes their login.',
      confirmLabel: 'Remove',
      onConfirm: () => widget.repo.deleteStaff(widget.staffId),
    );
    if (deleted && mounted) Navigator.pop(context, true);
  }

  Future<void> _editAccess() async {
    final current =
        (_staff?['account_roles'] as List?)?.cast<String>() ?? const [];
    final roles = await showDialog<List<String>>(
      context: context,
      builder: (context) => _RolePickerDialog(
        repo: widget.repo,
        selected: current,
        title: current.isEmpty ? 'Give app access' : 'Change roles',
        // The one thing the office needs told once: nobody here sets anyone's
        // credential, and that is deliberate.
        note: current.isEmpty
            ? 'They sign in with a one-time code sent to their own number and '
                  'choose their own PIN. No password is set for them.'
            : null,
      ),
    );
    if (roles == null) return;
    await _run(
      () => widget.repo.grantStaffAccount(widget.staffId, roles: roles),
    );
  }

  @override
  Widget build(BuildContext context) {
    final staff = _staff;
    if (staff == null) {
      return const SizedBox(
        height: 200,
        child: Center(child: CircularProgressIndicator()),
      );
    }

    final text = Theme.of(context).textTheme;
    final roles = (staff['account_roles'] as List?)?.cast<String>() ?? const [];
    final hasAccount = staff['user'] != null;
    final canManageStaff = widget.session.can('people.manage_staff');
    final canManageUsers = widget.session.can('admin.manage_users');

    return SafeArea(
      child: Padding(
        padding: EdgeInsets.fromLTRB(
          20,
          20,
          20,
          20 + MediaQuery.viewInsetsOf(context).bottom,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(staff['full_name'] as String? ?? '', style: text.titleLarge),
            const SizedBox(height: 4),
            Text(
              [
                staff['staff_number'],
                if ((staff['designation'] as String? ?? '').isNotEmpty)
                  staff['designation'],
                if ((staff['phone'] as String? ?? '').isNotEmpty)
                  formatPhone(staff['phone'] as String),
              ].join(' · '),
              style: text.bodyMedium,
            ),
            const SizedBox(height: 20),

            Text('Employment', style: text.labelLarge),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              children: [
                for (final entry in _staffStatuses.entries)
                  ChoiceChip(
                    label: Text(entry.value),
                    selected: staff['status'] == entry.key,
                    onSelected: _busy || !canManageStaff
                        ? null
                        : (_) => _setStatus(entry.key),
                  ),
              ],
            ),
            const SizedBox(height: 20),

            Text('App access', style: text.labelLarge),
            const SizedBox(height: 8),
            if (!hasAccount)
              Text(
                'No login. This person cannot open the app.',
                style: text.bodyMedium,
              )
            else if (roles.isEmpty)
              Text(
                'Signs in, but holds no role — they will see very little.',
                style: text.bodyMedium,
              )
            else
              Wrap(
                spacing: 8,
                children: [for (final role in roles) Chip(label: Text(role))],
              ),
            const SizedBox(height: 12),
            // OverflowBar, not Row: three buttons do not fit a narrow sheet,
            // and it stacks them instead of clipping one.
            OverflowBar(
              alignment: MainAxisAlignment.end,
              spacing: 8,
              overflowSpacing: 8,
              children: [
                if (canManageStaff)
                  TextButton.icon(
                    onPressed: _busy ? null : _delete,
                    icon: const Icon(Icons.person_remove_outlined),
                    label: const Text('Remove'),
                  ),
                TextButton(
                  onPressed: () => Navigator.pop(context, _changed),
                  child: const Text('Close'),
                ),
                if (canManageUsers)
                  FilledButton.icon(
                    onPressed: _busy ? null : _editAccess,
                    icon: const Icon(Icons.key_outlined),
                    label: Text(hasAccount ? 'Change roles' : 'Give access'),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _NewStaffDialog extends StatefulWidget {
  const _NewStaffDialog({required this.repo});

  final Repository repo;

  @override
  State<_NewStaffDialog> createState() => _NewStaffDialogState();
}

class _NewStaffDialogState extends State<_NewStaffDialog> {
  final _firstName = TextEditingController();
  final _lastName = TextEditingController();
  final _phone = TextEditingController();
  final _designation = TextEditingController();
  String _type = 'TEACHING';
  bool _saving = false;

  @override
  void dispose() {
    _firstName.dispose();
    _lastName.dispose();
    _phone.dispose();
    _designation.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_firstName.text.trim().isEmpty || _lastName.text.trim().isEmpty) {
      showApiMessage(context, 'A first and last name are required.');
      return;
    }
    // Shape-checked here so an obvious typo does not cost a round trip; the
    // server still has the last word, because only it knows the NCC ranges.
    // A staff record may legitimately carry no number — a caretaker hired off
    // a paper form — so blank is allowed and only a wrong one is refused.
    final typed = _phone.text.trim();
    var phone = '';
    if (typed.isNotEmpty) {
      final problem = phoneErrorFor(typed);
      if (problem != null) {
        showApiMessage(context, problem);
        return;
      }
      phone = normalizePhone(typed);
    }

    setState(() => _saving = true);
    try {
      await widget.repo.createStaff({
        'first_name': _firstName.text.trim(),
        'last_name': _lastName.text.trim(),
        'phone': phone,
        'designation': _designation.text.trim(),
        'staff_type': _type,
      });
      if (mounted) Navigator.pop(context, true);
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Add staff'),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextField(
            controller: _firstName,
            decoration: const InputDecoration(labelText: 'First name'),
            textInputAction: TextInputAction.next,
          ),
          TextField(
            controller: _lastName,
            decoration: const InputDecoration(labelText: 'Last name'),
            textInputAction: TextInputAction.next,
          ),
          TextField(
            controller: _phone,
            decoration: const InputDecoration(
              labelText: 'Phone',
              helperText: 'Needed later to give them a login',
            ),
            keyboardType: TextInputType.phone,
          ),
          TextField(
            controller: _designation,
            decoration: const InputDecoration(labelText: 'Designation'),
          ),
          const SizedBox(height: 16),
          SegmentedButton<String>(
            segments: const [
              ButtonSegment(value: 'TEACHING', label: Text('Teaching')),
              ButtonSegment(value: 'NON_TEACHING', label: Text('Non-teaching')),
            ],
            selected: {_type},
            onSelectionChanged: (values) =>
                setState(() => _type = values.first),
          ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: _saving ? null : () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(onPressed: _saving ? null : _save, child: const Text('Add')),
    ],
  );
}

// ---------------------------------------------------------------------------
// Accounts
// ---------------------------------------------------------------------------

class _AccountsTab extends StatefulWidget {
  const _AccountsTab({required this.session});

  final Session session;

  @override
  State<_AccountsTab> createState() => _AccountsTabState();
}

class _AccountsTabState extends State<_AccountsTab> {
  final _key = GlobalKey<AsyncViewState<List<Map<String, dynamic>>>>();
  late final Repository _repo = Repository(widget.session.api);
  String _search = '';
  bool? _active = true;

  void _reload() => _key.currentState?.reload();

  Future<void> _add() async {
    final created = await showDialog<bool>(
      context: context,
      builder: (context) => _NewUserDialog(repo: _repo),
    );
    if (created == true) _reload();
  }

  Future<void> _open(Map<String, dynamic> row) async {
    final changed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      // No width override: Material 3 already caps the sheet at 640 and
      // centres it, which is what a form wants on a tablet or a browser.
      builder: (context) =>
          _AccountSheet(repo: _repo, userId: row['id'] as String),
    );
    if (changed == true) _reload();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    floatingActionButton: FloatingActionButton.extended(
      onPressed: _add,
      icon: const Icon(Icons.person_add_alt_1_outlined),
      label: const Text('New account'),
    ),
    body: Column(
      children: [
        _SearchField(
          hint: 'Name or phone number',
          onSubmitted: (value) {
            setState(() => _search = value.trim());
            _reload();
          },
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Row(
            children: [
              for (final entry in const {
                'true': 'Active',
                'false': 'Disabled',
                '': 'All',
              }.entries)
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    label: Text(entry.value),
                    selected: (_active?.toString() ?? '') == entry.key,
                    onSelected: (_) {
                      setState(
                        () => _active = entry.key.isEmpty
                            ? null
                            : entry.key == 'true',
                      );
                      _reload();
                    },
                  ),
                ),
            ],
          ),
        ),
        Expanded(
          child: AsyncView<List<Map<String, dynamic>>>(
            key: _key,
            load: () => _repo.users(
              active: _active,
              search: _search.isEmpty ? null : _search,
            ),
            empty: const EmptyState(
              icon: Icons.manage_accounts_outlined,
              message: 'No accounts match this filter.',
            ),
            builder: (context, users) => ListView.separated(
              itemCount: users.length,
              separatorBuilder: (_, _) => const Divider(height: 1),
              itemBuilder: (context, index) {
                final row = users[index];
                final active = row['is_active'] != false;
                return ListTile(
                  leading: CircleAvatar(
                    child: Text(_initials(row['full_name'] as String? ?? '')),
                  ),
                  title: Text(
                    (row['full_name'] as String? ?? '').isEmpty
                        ? (row['phone_display'] as String? ?? 'Unnamed')
                        : row['full_name'] as String,
                  ),
                  subtitle: Text(row['phone_display'] as String? ?? ''),
                  trailing: active
                      ? null
                      : const Chip(
                          label: Text('Disabled'),
                          visualDensity: VisualDensity.compact,
                        ),
                  onTap: () => _open(row),
                );
              },
            ),
          ),
        ),
      ],
    ),
  );
}

class _AccountSheet extends StatefulWidget {
  const _AccountSheet({required this.repo, required this.userId});

  final Repository repo;
  final String userId;

  @override
  State<_AccountSheet> createState() => _AccountSheetState();
}

class _AccountSheetState extends State<_AccountSheet> {
  Map<String, dynamic>? _user;
  bool _busy = false;
  bool _changed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final user = await widget.repo.user(widget.userId);
      if (mounted) setState(() => _user = user);
    } on ApiError catch (e) {
      if (mounted) {
        showApiMessage(context, e.message);
        Navigator.pop(context, _changed);
      }
    }
  }

  Future<void> _run(Future<Map<String, dynamic>> Function() action) async {
    setState(() => _busy = true);
    try {
      final updated = await action();
      if (mounted) setState(() => _user = updated);
      _changed = true;
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _editRoles() async {
    final current = (_user?['roles'] as List?)?.cast<String>() ?? const [];
    final roles = await showDialog<List<String>>(
      context: context,
      builder: (context) => _RolePickerDialog(
        repo: widget.repo,
        selected: current,
        title: 'Roles',
      ),
    );
    if (roles == null) return;
    await _run(() => widget.repo.setUserRoles(widget.userId, roles));
  }

  Future<void> _setActive(bool active) async {
    if (!active) {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Disable this account?'),
          content: const Text(
            'They are signed out of every phone immediately and cannot sign '
            'in again until this is reversed.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Disable'),
            ),
          ],
        ),
      );
      if (confirmed != true) return;
    }
    await _run(() => widget.repo.setUserActive(widget.userId, active));
  }

  @override
  Widget build(BuildContext context) {
    final user = _user;
    if (user == null) {
      return const SizedBox(
        height: 200,
        child: Center(child: CircularProgressIndicator()),
      );
    }

    final text = Theme.of(context).textTheme;
    final roles = (user['roles'] as List?)?.cast<String>() ?? const [];
    final active = user['is_active'] != false;

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              (user['full_name'] as String? ?? '').isEmpty
                  ? 'Unnamed account'
                  : user['full_name'] as String,
              style: text.titleLarge,
            ),
            Text(
              user['phone_display'] as String? ?? '',
              style: text.bodyMedium,
            ),
            const SizedBox(height: 20),
            Text('Roles', style: text.labelLarge),
            const SizedBox(height: 8),
            if (roles.isEmpty)
              Text(
                'No role — this account can see very little.',
                style: text.bodyMedium,
              )
            else
              Wrap(
                spacing: 8,
                children: [for (final role in roles) Chip(label: Text(role))],
              ),
            const SizedBox(height: 8),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Can sign in'),
              value: active,
              onChanged: _busy ? null : _setActive,
            ),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton(
                  onPressed: () => Navigator.pop(context, _changed),
                  child: const Text('Close'),
                ),
                const SizedBox(width: 8),
                FilledButton.icon(
                  onPressed: _busy ? null : _editRoles,
                  icon: const Icon(Icons.key_outlined),
                  label: const Text('Change roles'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _NewUserDialog extends StatefulWidget {
  const _NewUserDialog({required this.repo});

  final Repository repo;

  @override
  State<_NewUserDialog> createState() => _NewUserDialogState();
}

class _NewUserDialogState extends State<_NewUserDialog> {
  final _phone = TextEditingController();
  final _firstName = TextEditingController();
  final _lastName = TextEditingController();
  final _roles = <String>{};
  bool _saving = false;

  @override
  void dispose() {
    _phone.dispose();
    _firstName.dispose();
    _lastName.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final problem = phoneErrorFor(_phone.text.trim());
    if (problem != null) {
      showApiMessage(context, problem);
      return;
    }
    setState(() => _saving = true);
    try {
      await widget.repo.createUser(
        phone: normalizePhone(_phone.text.trim()),
        firstName: _firstName.text.trim(),
        lastName: _lastName.text.trim(),
        roles: _roles.toList(),
      );
      if (mounted) Navigator.pop(context, true);
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('New account'),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: _phone,
            decoration: const InputDecoration(labelText: 'Phone'),
            keyboardType: TextInputType.phone,
            textInputAction: TextInputAction.next,
          ),
          TextField(
            controller: _firstName,
            decoration: const InputDecoration(labelText: 'First name'),
            textInputAction: TextInputAction.next,
          ),
          TextField(
            controller: _lastName,
            decoration: const InputDecoration(labelText: 'Last name'),
          ),
          const SizedBox(height: 16),
          _RoleChips(
            repo: widget.repo,
            selected: _roles,
            onChanged: () => setState(() {}),
          ),
          const SizedBox(height: 12),
          Text(
            'They sign in with a one-time code sent to that number and choose '
            'their own PIN. No password is set here.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: _saving ? null : () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: _saving ? null : _save,
        child: const Text('Create'),
      ),
    ],
  );
}

// ---------------------------------------------------------------------------
// Roles
// ---------------------------------------------------------------------------

/// The school's own roles, fetched rather than hard-coded: a school that
/// invents "Head of Exams" gets it here without a client release.
class _RoleChips extends StatelessWidget {
  const _RoleChips({
    required this.repo,
    required this.selected,
    required this.onChanged,
  });

  final Repository repo;
  final Set<String> selected;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) =>
      FutureBuilder<List<Map<String, dynamic>>>(
        future: repo.roles(),
        builder: (context, snapshot) {
          if (!snapshot.hasData) {
            return const Padding(
              padding: EdgeInsets.all(8),
              child: LinearProgressIndicator(),
            );
          }
          return Wrap(
            spacing: 8,
            children: [
              for (final role in snapshot.data!)
                FilterChip(
                  label: Text(
                    role['name'] as String? ?? role['code'] as String,
                  ),
                  selected: selected.contains(role['code']),
                  onSelected: (on) {
                    on
                        ? selected.add(role['code'] as String)
                        : selected.remove(role['code']);
                    onChanged();
                  },
                ),
            ],
          );
        },
      );
}

class _RolePickerDialog extends StatefulWidget {
  const _RolePickerDialog({
    required this.repo,
    required this.selected,
    required this.title,
    this.note,
  });

  final Repository repo;
  final List<String> selected;
  final String title;
  final String? note;

  @override
  State<_RolePickerDialog> createState() => _RolePickerDialogState();
}

class _RolePickerDialogState extends State<_RolePickerDialog> {
  late final Set<String> _selected = {...widget.selected};

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(widget.title),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _RoleChips(
            repo: widget.repo,
            selected: _selected,
            onChanged: () => setState(() {}),
          ),
          if (widget.note != null) ...[
            const SizedBox(height: 16),
            Text(widget.note!, style: Theme.of(context).textTheme.bodySmall),
          ],
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        // The whole set, not a delta: the server records what they had before
        // and what they hold now, and two clients cannot disagree about it.
        onPressed: () => Navigator.pop(context, _selected.toList()),
        child: const Text('Save'),
      ),
    ],
  );
}

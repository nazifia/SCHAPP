import 'package:flutter/material.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';

/// School notices. Everyone reads; whoever holds
/// `communication.manage_announcement` can also write and publish one.
///
/// A draft is deliberately visible to its author before publishing, because
/// publishing is what texts nine hundred parents and there is no undo.
class NoticesScreen extends StatefulWidget {
  const NoticesScreen({super.key, required this.session});

  final Session session;

  @override
  State<NoticesScreen> createState() => _NoticesScreenState();
}

class _NoticesScreenState extends State<NoticesScreen> {
  final _key = GlobalKey<AsyncViewState<List<Map<String, dynamic>>>>();
  late final Repository _repo = Repository(widget.session.api);

  @override
  void initState() {
    super.initState();
    // Opening the screen is what marks the inbox read; the badge on the
    // dashboard is the only place an unread count is shown.
    _repo.markInboxRead().catchError((_) {});
  }

  Future<void> _compose() async {
    final created = await showDialog<bool>(
      context: context,
      builder: (context) => _ComposeDialog(repo: _repo),
    );
    if (created == true) _key.currentState?.reload();
  }

  @override
  Widget build(BuildContext context) {
    final canManage = widget.session.can('communication.manage_announcement');
    return Scaffold(
      appBar: AppBar(title: const Text('Notices')),
      floatingActionButton: canManage
          ? FloatingActionButton.extended(
              onPressed: _compose,
              icon: const Icon(Icons.campaign_outlined),
              label: const Text('New notice'),
            )
          : null,
      body: AsyncView<List<Map<String, dynamic>>>(
        key: _key,
        load: _repo.announcements,
        empty: const EmptyState(
          icon: Icons.campaign_outlined,
          message: 'No notices yet.',
        ),
        builder: (context, notices) => ListView.separated(
          itemCount: notices.length,
          separatorBuilder: (_, _) => const Divider(height: 1),
          itemBuilder: (context, index) {
            final notice = notices[index];
            final published = notice['published_at'] != null;
            return ListTile(
              leading: Icon(
                notice['is_pinned'] == true
                    ? Icons.push_pin_outlined
                    : Icons.article_outlined,
              ),
              title: Text(notice['title'] as String? ?? ''),
              subtitle: Text(
                notice['body'] as String? ?? '',
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
              trailing: published
                  ? Text('${notice['recipients_count'] ?? 0}')
                  : canManage
                  ? TextButton(
                      onPressed: () => _publish(notice),
                      child: const Text('Publish'),
                    )
                  : null,
              onTap: () => _open(notice),
            );
          },
        ),
      ),
    );
  }

  void _open(Map<String, dynamic> notice) => showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(notice['title'] as String? ?? ''),
      content: SingleChildScrollView(
        child: Text(notice['body'] as String? ?? ''),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Close'),
        ),
      ],
    ),
  );

  Future<void> _publish(Map<String, dynamic> notice) async {
    final channels = (notice['channels'] as List?)?.cast<String>() ?? const [];
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Publish this notice?'),
        content: Text(
          channels.isEmpty
              ? 'It appears in the app for everyone in the audience.'
              : 'It appears in the app and is sent by '
                    '${channels.join(' and ').toLowerCase()}. '
                    'Messages cost money and cannot be recalled.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Publish'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await _repo.publishAnnouncement(notice['id'] as String);
      _key.currentState?.reload();
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    }
  }
}

class _ComposeDialog extends StatefulWidget {
  const _ComposeDialog({required this.repo});

  final Repository repo;

  @override
  State<_ComposeDialog> createState() => _ComposeDialogState();
}

class _ComposeDialogState extends State<_ComposeDialog> {
  final _title = TextEditingController();
  final _body = TextEditingController();
  final _audience = <String>{};
  final _channels = <String>{};
  bool _saving = false;

  @override
  void dispose() {
    _title.dispose();
    _body.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_title.text.trim().isEmpty || _body.text.trim().isEmpty) return;
    setState(() => _saving = true);
    try {
      await widget.repo.createAnnouncement(
        title: _title.text.trim(),
        body: _body.text.trim(),
        audienceRoles: _audience.toList(),
        channels: _channels.toList(),
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
    title: const Text('New notice'),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: _title,
            decoration: const InputDecoration(labelText: 'Title'),
            textInputAction: TextInputAction.next,
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _body,
            decoration: const InputDecoration(labelText: 'Message'),
            maxLines: 5,
          ),
          const SizedBox(height: 16),
          const Text('Audience — none selected means everyone'),
          Wrap(
            spacing: 8,
            children: [
              for (final role in const ['guardian', 'student', 'teacher'])
                FilterChip(
                  label: Text(role),
                  selected: _audience.contains(role),
                  onSelected: (on) => setState(
                    () => on ? _audience.add(role) : _audience.remove(role),
                  ),
                ),
            ],
          ),
          const SizedBox(height: 12),
          const Text('Also send by'),
          Wrap(
            spacing: 8,
            children: [
              for (final channel in const ['SMS', 'EMAIL', 'PUSH'])
                FilterChip(
                  label: Text(channel),
                  selected: _channels.contains(channel),
                  onSelected: (on) => setState(
                    () =>
                        on ? _channels.add(channel) : _channels.remove(channel),
                  ),
                ),
            ],
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
        child: const Text('Save draft'),
      ),
    ],
  );
}

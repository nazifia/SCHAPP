import 'package:flutter/material.dart';

import '../api/repository.dart';
import '../auth/session.dart';
import '../design/async_view.dart';
import '../design/theme.dart';

/// "The parents say they never got the text."
///
/// Every message the school sent, with what the provider said back. The
/// delivery reports were being written and nothing read them, so a failed
/// batch looked exactly like a delivered one from inside the app — the bursar
/// found out when a parent turned up not knowing fees were due.
///
/// Failures first is deliberate: nobody opens this screen to admire the
/// messages that worked.
class DeliveryScreen extends StatefulWidget {
  const DeliveryScreen({super.key, required this.session});

  final Session session;

  @override
  State<DeliveryScreen> createState() => _DeliveryScreenState();
}

const _filters = {
  'FAILED': 'Failed',
  'SENT': 'Sent',
  'DELIVERED': 'Delivered',
  '': 'All',
};

class _DeliveryScreenState extends State<DeliveryScreen> {
  final _key = GlobalKey<AsyncViewState<List<Map<String, dynamic>>>>();
  late final Repository _repo = Repository(widget.session.api);
  String _status = 'FAILED';
  String? _channel;

  void _set(void Function() change) {
    setState(change);
    // AsyncView loads once; a filter change has to ask for the new one.
    _key.currentState?.reload();
  }

  static Widget _badge(String status) => switch (status) {
    'DELIVERED' => StatusBadge.success(status),
    'FAILED' => StatusBadge.error(status),
    'SENT' => StatusBadge.info(status),
    _ => StatusBadge.warning(status.isEmpty ? 'QUEUED' : status),
  };

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(title: const Text('Message delivery')),
      body: Column(
        children: [
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.all(12),
            child: Row(
              children: [
                for (final entry in _filters.entries)
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: ChoiceChip(
                      label: Text(entry.value),
                      selected: _status == entry.key,
                      onSelected: (_) => _set(() => _status = entry.key),
                    ),
                  ),
                const SizedBox(width: 8),
                // In-app copies are always "sent" and always arrive; the
                // question this screen answers is about SMS and email.
                for (final channel in const ['SMS', 'EMAIL', 'PUSH'])
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: FilterChip(
                      label: Text(channel),
                      selected: _channel == channel,
                      onSelected: (on) =>
                          _set(() => _channel = on ? channel : null),
                    ),
                  ),
              ],
            ),
          ),
          Expanded(
            child: AsyncView<List<Map<String, dynamic>>>(
              key: _key,
              load: () => _repo.messageLog(
                status: _status.isEmpty ? null : _status,
                channel: _channel,
              ),
              empty: const EmptyState(
                icon: Icons.mark_email_read_outlined,
                message: 'Nothing here — which is the good outcome.',
              ),
              builder: (context, messages) => ListView.separated(
                itemCount: messages.length,
                separatorBuilder: (_, _) => const Divider(height: 1),
                itemBuilder: (context, index) {
                  final message = messages[index];
                  final error = message['error'] as String? ?? '';
                  return ListTile(
                    title: Text(
                      message['destination'] as String? ??
                          message['subject'] as String? ??
                          '',
                    ),
                    subtitle: Text(
                      [
                        message['channel'] ?? '',
                        '${message['sent_at'] ?? message['created_at'] ?? ''}'
                            .replaceFirst('T', ' ')
                            .split('.')
                            .first,
                        if (error.isNotEmpty) error,
                      ].join(' · '),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    trailing: _badge(message['status'] as String? ?? ''),
                    onTap: () => _open(message),
                  );
                },
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Text(
              'Sent means the provider took it. Delivered means the handset '
              'confirmed it — some networks never do.',
              style: text.bodySmall,
            ),
          ),
        ],
      ),
    );
  }

  void _open(Map<String, dynamic> message) => showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(message['destination'] as String? ?? 'Message'),
      content: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(message['body'] as String? ?? ''),
            const SizedBox(height: 12),
            for (final field in const [
              'channel',
              'provider',
              'status',
              'delivery_status',
              'sent_at',
              'delivered_at',
              'error',
            ])
              if ('${message[field] ?? ''}'.isNotEmpty)
                Text('${field.replaceAll('_', ' ')}: ${message[field]}'),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Close'),
        ),
      ],
    ),
  );
}

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:printing/printing.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';
import '../design/theme.dart';

/// Fees: what is owed, and the button that pays it.
///
/// The list is what the API already scopes — a parent sees their children's
/// bills, a bursar sees the school's.
///
/// Offline, the counter still works: billing, issuing, waiving, adjusting and
/// taking a payment all queue, and `Idempotency-Key` is what keeps a replayed
/// receipt from being money counted twice. The *gateway* flow does not queue —
/// a checkout URL and a verification are round trips to the provider, and
/// neither means anything produced after the payer has walked away.
class FeesScreen extends StatefulWidget {
  const FeesScreen({super.key, required this.session});

  final Session session;

  @override
  State<FeesScreen> createState() => _FeesScreenState();
}

class _FeesScreenState extends State<FeesScreen> {
  final _key = GlobalKey<AsyncViewState<List<Map<String, dynamic>>>>();
  late final Repository _repo = Repository(widget.session.api);
  bool _busy = false;

  /// Which bill the second column is showing. Held as an id, not the row: the
  /// list is re-read after every write and the old map is a different object.
  String? _selectedId;

  /// The counter lookup. A bursar with a parent in front of them knows the
  /// child's name or the number on the printed bill, not which page of three
  /// thousand invoices it is on.
  String _search = '';

  /// The chip that is on. `owing` is the server's own filter — issued or
  /// part-paid *and* still short — which is the bursary's working list and so
  /// the default; the rest are plain statuses.
  String _filter = 'owing';

  static const _filters = {
    'owing': 'Owing',
    '': 'All',
    'DRAFT': 'Draft',
    'PAID': 'Paid',
    'WAIVED': 'Waived',
    'CANCELLED': 'Cancelled',
  };

  Future<List<Map<String, dynamic>>> _load() => _repo.invoices(
    owing: _filter == 'owing' ? true : null,
    status: (_filter == 'owing' || _filter.isEmpty) ? null : _filter,
    search: _search.isEmpty ? null : _search,
  );

  /// Re-read with the current filter, and drop the pick from the pane: the
  /// selected bill may not be in the new list at all.
  void _reload() {
    setState(() => _selectedId = null);
    _key.currentState?.reload();
  }

  @override
  Widget build(BuildContext context) {
    final canRecordPayment = widget.session.can('finance.record_payment');
    final canManageFees = widget.session.can('finance.manage_fees');
    return Scaffold(
      appBar: AppBar(
        title: const Text('Fees'),
        actions: [
          // Where a price is changed for a whole cohort. Every dialog on this
          // screen edits one family's bill; that one edits what everyone is
          // charged, and the drafts follow it.
          if (canManageFees)
            IconButton(
              onPressed: () => context.go('/fee-structures'),
              icon: const Icon(Icons.price_change_outlined),
              tooltip: 'Fees & price lists',
            ),
          if (canManageFees)
            IconButton(
              onPressed: _busy ? null : _generate,
              icon: const Icon(Icons.playlist_add_outlined),
              tooltip: 'Raise invoices',
            ),
          if (canRecordPayment)
            IconButton(
              onPressed: _busy ? null : _sweep,
              icon: const Icon(Icons.sync_problem_outlined),
              tooltip: 'Check unconfirmed payments',
            ),
        ],
      ),
      body: Column(
        children: [
          _finder(),
          Expanded(
            child: AsyncView<List<Map<String, dynamic>>>(
              key: _key,
              load: _load,
              empty: EmptyState(
                icon: Icons.receipt_long_outlined,
                message: _search.isEmpty
                    ? 'No invoices in this list.'
                    : 'Nothing matches "$_search". Try the admission number, '
                          'or widen the filter to All.',
              ),
              builder: _body,
            ),
          ),
        ],
      ),
    );
  }

  /// The counter's two controls: type who is paying, then narrow by state.
  /// Both re-read from the server — a bursar searching for one child must not
  /// be searching only the fifty bills that happened to be on screen.
  Widget _finder() => Column(
    children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
        child: TextField(
          decoration: const InputDecoration(
            hintText: 'Name, admission number or invoice number',
            prefixIcon: Icon(Icons.search),
            isDense: true,
            border: OutlineInputBorder(),
          ),
          textInputAction: TextInputAction.search,
          // On submit, not per keystroke: same reason as the student
          // register — a school has thousands of bills and this network does
          // not spare a round trip per letter.
          onSubmitted: (value) {
            setState(() => _search = value.trim());
            _reload();
          },
        ),
      ),
      SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        child: Row(
          children: [
            for (final entry in _filters.entries)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 4),
                child: ChoiceChip(
                  label: Text(entry.value),
                  selected: _filter == entry.key,
                  onSelected: (_) {
                    setState(() => _filter = entry.key);
                    _reload();
                  },
                ),
              ),
          ],
        ),
      ),
    ],
  );

  Widget _body(BuildContext context, List<Map<String, dynamic>> invoices) {
    final outstanding = invoices.fold<double>(
      0,
      (sum, invoice) => sum + (double.tryParse('${invoice['balance']}') ?? 0),
    );
    // ponytail: a Wrap, not a ListTile — a `trailing` of ₦12,500,000.00 in
    // titleLarge is wider than a 320dp tile, and ListTile asserts on that
    // rather than reflowing. A Wrap drops the amount onto its own line
    // instead, which is the one thing that must never be clipped.
    final total = Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Wrap(
          alignment: WrapAlignment.spaceBetween,
          crossAxisAlignment: WrapCrossAlignment.center,
          spacing: 8,
          runSpacing: 4,
          children: [
            // "On this list", not "total": it is the sum of what is loaded and
            // filtered, and a bursar reading it as the school's arrears would
            // be reading a number nobody computed.
            const Text('Outstanding on this list'),
            Text(
              naira(outstanding),
              style: Theme.of(context).textTheme.titleLarge,
            ),
          ],
        ),
      ),
    );
    Widget card(Map<String, dynamic> invoice) => _InvoiceCard(
      invoice: invoice,
      onPay: () => _pay(invoice),
      onAction: (action) => _act(action, invoice),
      session: widget.session,
    );

    return LayoutBuilder(
      builder: (context, box) {
        // One column: the card already carries the whole bill, so the
        // list and the detail are the same thing and nothing is hidden.
        if (box.maxWidth < kTwoColumnFrom) {
          return ListView(
            padding: const EdgeInsets.all(12),
            children: [total, for (final invoice in invoices) card(invoice)],
          );
        }
        // Two columns: a stack of full cards wastes a wide window, so
        // the list goes compact and one bill gets the room. The last
        // pick may have been cancelled or paid off out from under us —
        // fall back to the first rather than an empty pane.
        final selected = invoices.firstWhere(
          (invoice) => invoice['id'] == _selectedId,
          orElse: () => invoices.first,
        );
        return Row(
          children: [
            SizedBox(
              width: kMasterWidth,
              child: ListView(
                padding: const EdgeInsets.all(12),
                children: [
                  total,
                  for (final invoice in invoices)
                    ListTile(
                      selected: invoice['id'] == selected['id'],
                      title: Text(
                        invoice['full_name'] as String? ?? '',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      subtitle: Text('${invoice['number']}'),
                      trailing: Text(naira(invoice['balance'])),
                      onTap: () =>
                          setState(() => _selectedId = invoice['id'] as String),
                    ),
                ],
              ),
            ),
            const VerticalDivider(width: 1),
            Expanded(
              child: ListView(
                padding: const EdgeInsets.all(12),
                children: [card(selected)],
              ),
            ),
          ],
        );
      },
    );
  }

  /// One entry point for everything on the invoice menu, so the reload and
  /// the error handling are written once.
  Future<void> _act(String action, Map<String, dynamic> invoice) async {
    final id = invoice['id'] as String;
    final number = invoice['number'] as String? ?? 'this invoice';
    switch (action) {
      case 'issue':
        await _run(
          () => _repo.issueInvoice(id),
          done: 'Issued. $number is now visible to the payer.',
        );
      case 'waive':
        final reason = await _ask(
          title: 'Waive $number?',
          blurb:
              'The balance is written off and the bill is kept, marked '
              'waived. The reason is what an auditor reads back.',
          hint: 'Staff child — fees remitted',
          confirmLabel: 'Waive',
        );
        if (reason == null) return;
        await _run(
          () => _repo.waiveInvoice(id, reason: reason),
          done: 'Waived.',
        );
      case 'cancel':
        final reason = await _ask(
          title: 'Cancel $number?',
          blurb:
              'For a bill raised in error. If money has already been taken '
              'against it, reverse the payment first — the server will '
              'refuse until you do.',
          hint: 'Raised against the wrong student',
          confirmLabel: 'Cancel invoice',
        );
        if (reason == null) return;
        await _run(
          () => _repo.cancelInvoice(id, reason: reason),
          done: 'Cancelled.',
        );
      case 'adjust':
        await _adjust(invoice);
      case 'pay':
        await _recordPayment(invoice);
      case 'reverse':
        await _reverse(invoice);
      case 'document':
        await _run(
          () async => Printing.sharePdf(
            bytes: await _repo.invoiceDocument(id),
            filename: '$number.pdf',
          ),
        );
    }
  }

  /// Run a write, show the API's message on failure, re-read the list.
  Future<void> _run(Future<void> Function() action, {String? done}) async {
    setState(() => _busy = true);
    try {
      await action();
      if (mounted && done != null) showApiMessage(context, done);
      _key.currentState?.reload();
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// A decision that is kept on the record for good needs its why typed in.
  Future<String?> _ask({
    required String title,
    required String blurb,
    required String hint,
    required String confirmLabel,
  }) => showDialog<String>(
    context: context,
    builder: (context) => _ReasonDialog(
      title: title,
      blurb: blurb,
      hint: hint,
      confirmLabel: confirmLabel,
    ),
  );

  /// Cash, transfer, POS or cheque taken at the counter. The receipt number
  /// is the server's to issue, from the school's own numbering format.
  Future<void> _recordPayment(Map<String, dynamic> invoice) async {
    final balance = double.tryParse('${invoice['balance']}') ?? 0;
    final entry = await showDialog<Map<String, String>>(
      context: context,
      builder: (context) => _CounterPaymentDialog(balance: balance),
    );
    if (entry == null) return;
    await _run(() async {
      final payment = await _repo.recordPayment(
        invoice['id'] as String,
        amount: entry['amount']!,
        method: entry['method']!,
        payerName: entry['payer_name'] ?? '',
        note: entry['note'] ?? '',
      );
      if (!mounted) return;
      // Straight to the receipt: the payer is still standing at the counter,
      // and a receipt they have to come back for is a receipt they chase.
      final printed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text('Receipt ${payment['reference']}'),
          content: Text('${naira(payment['amount'])} recorded.'),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Later'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Print receipt'),
            ),
          ],
        ),
      );
      if (printed != true) return;
      await Printing.sharePdf(
        bytes: await _repo.receipt(payment['id'] as String),
        filename: '${payment['reference']}.pdf',
      );
    });
  }

  /// Change what one family owes — a sibling rebate, a boarder who moved to
  /// day, a levy charged in error — with the reason on the record.
  ///
  /// The dialog opens on the bill as it stands rather than on an empty form:
  /// the whole set of lines is what gets sent back, so anything left untouched
  /// has to go back exactly as it came.
  Future<void> _adjust(Map<String, dynamic> invoice) async {
    final edit = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (context) => _AdjustDialog(invoice: invoice),
    );
    if (edit == null) return;
    await _run(
      () => _repo.adjustInvoice(
        invoice['id'] as String,
        lines: (edit['lines'] as List).cast<Map<String, dynamic>>(),
        discount: edit['discount'] as String?,
        reason: edit['reason'] as String,
      ),
      done: 'Adjusted. The balance is what is owed now.',
    );
  }

  /// Bill a whole cohort from a price list. The report matters as much as the
  /// success: a run that skipped four students because their records were
  /// removed has to say so, not just say "done".
  Future<void> _generate() async {
    final List<Map<String, dynamic>> structures;
    setState(() => _busy = true);
    try {
      structures = await _repo.feeStructures(active: true);
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
      return;
    } finally {
      if (mounted) setState(() => _busy = false);
    }
    if (!mounted) return;
    if (structures.isEmpty) {
      showApiMessage(
        context,
        'No active price list to bill from. Build one under Fees & price '
        'lists first.',
      );
      return;
    }

    final structure = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (context) => SimpleDialog(
        title: const Text('Bill which price list?'),
        children: [
          for (final row in structures)
            SimpleDialogOption(
              onPressed: () => Navigator.pop(context, row),
              child: ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(row['name'] as String? ?? ''),
                subtitle: Text(
                  '${(row['items'] as List? ?? const []).length} items',
                ),
                trailing: Text(naira(row['total'])),
              ),
            ),
        ],
      ),
    );
    if (structure == null || !mounted) return;

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Raise these invoices?'),
        content: Text(
          'Every student in the cohort for "${structure['name']}" gets a '
          'draft invoice of ${naira(structure['total'])}.\n\n'
          'Running this again is safe: anyone already billed from this list '
          'is left exactly as they are.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Raise invoices'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    setState(() => _busy = true);
    try {
      final report = await _repo.generateInvoices(
        structure: structure['id'] as String,
      );
      if (!mounted) return;
      // An empty report is a queued run: the bills are raised when the phone
      // finds a network, so there is no per-row outcome to show yet.
      if (report.isEmpty) {
        showApiMessage(
          context,
          'Billing run saved on this device. The invoices will be raised when '
          'there is a connection.',
        );
      } else {
        await showDialog<void>(
          context: context,
          builder: (context) => _BillingReportDialog(report: report),
        );
      }
      _key.currentState?.reload();
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Re-ask the gateway about every checkout its webhook never reported on.
  /// It runs half-hourly by itself; this is the button for the bursar with a
  /// parent standing at the desk saying the money left their account.
  Future<void> _sweep() async {
    setState(() => _busy = true);
    try {
      final result = await _repo.sweepPayments();
      if (!mounted) return;
      final checked = result['checked'] ?? 0;
      final settled = result['settled'] ?? 0;
      final abandoned = result['abandoned'] ?? 0;
      showApiMessage(
        context,
        checked == 0 && abandoned == 0
            ? 'Nothing was waiting on the bank.'
            : '$checked checked, $settled confirmed, '
                  '$abandoned given up on as abandoned.',
      );
      _key.currentState?.reload();
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Reverse a receipt: a bounced cheque, a failed settlement, a payment
  /// keyed against the wrong bill. The row stays — money is never deleted —
  /// and the invoice balance goes back up, so this is picked from the
  /// invoice's own receipts rather than typed in free.
  Future<void> _reverse(Map<String, dynamic> invoice) async {
    final List<Map<String, dynamic>> receipts;
    try {
      receipts = await _repo.payments(
        invoice: invoice['id'] as String,
        status: 'SUCCESS',
      );
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
      return;
    }
    if (!mounted) return;
    if (receipts.isEmpty) {
      showApiMessage(context, 'No settled payment on this invoice to reverse.');
      return;
    }

    final payment = receipts.length == 1
        ? receipts.first
        : await showDialog<Map<String, dynamic>>(
            context: context,
            builder: (context) => SimpleDialog(
              title: const Text('Which payment?'),
              children: [
                for (final receipt in receipts)
                  SimpleDialogOption(
                    onPressed: () => Navigator.pop(context, receipt),
                    child: Text(
                      '${naira(receipt['amount'])} · ${receipt['reference']}',
                    ),
                  ),
              ],
            ),
          );
    if (payment == null || !mounted) return;

    final reason = await _ask(
      title: 'Reverse ${naira(payment['amount'])}?',
      blurb:
          'Reference ${payment['reference']}. The receipt is kept and marked '
          'reversed, and the balance is owed again.',
      hint: 'Cheque returned unpaid',
      confirmLabel: 'Reverse',
    );
    if (reason == null) return;
    await _run(
      () => _repo.reversePayment(payment['id'] as String, reason: reason),
      done: 'Reversed. The balance is owed again.',
    );
  }

  Future<void> _pay(Map<String, dynamic> invoice) async {
    final List<String> gateways;
    try {
      gateways = await _repo.paymentGateways();
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
      return;
    }
    if (!mounted) return;
    if (gateways.isEmpty) {
      showApiMessage(
        context,
        'Online payment is not set up for this school. Pay at the bursary.',
      );
      return;
    }

    final gateway = gateways.length == 1
        ? gateways.first
        : await showDialog<String>(
            context: context,
            builder: (context) => SimpleDialog(
              title: const Text('Pay with'),
              children: [
                for (final name in gateways)
                  SimpleDialogOption(
                    onPressed: () => Navigator.pop(context, name),
                    child: Text(name),
                  ),
              ],
            ),
          );
    if (gateway == null || !mounted) return;

    try {
      final checkout = await _repo.checkout(
        invoiceId: invoice['id'] as String,
        gateway: gateway,
      );
      final url = Uri.parse(checkout['checkout_url'] as String);
      // External application: the gateway's 3-D Secure and bank redirects do
      // not survive an in-app webview on every Nigerian bank.
      await launchUrl(url, mode: LaunchMode.externalApplication);
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Finish in your browser'),
          content: Text(
            'Reference ${checkout['reference']}.\n\n'
            'Come back here and tap Done once you have paid — we will check '
            'with the bank straight away.',
          ),
          actions: [
            FilledButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Done'),
            ),
          ],
        ),
      );
      if (!mounted) return;
      await _confirm(checkout['payment'] as String);
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    }
  }

  /// Ask the gateway directly instead of waiting on its webhook. A parent who
  /// has just paid is standing there; the webhook may be minutes away or lost,
  /// and the half-hourly sweep is for the ones nobody is watching. Safe to call
  /// repeatedly — the server treats a re-verify as a no-op once it has settled.
  Future<void> _confirm(String paymentId) async {
    try {
      final payment = await _repo.verifyPayment(paymentId);
      if (!mounted) return;
      showApiMessage(
        context,
        payment['status'] == 'SUCCESS'
            ? 'Payment confirmed. Your receipt is ready.'
            : 'The bank has not confirmed this payment yet. It will appear '
                  'here as soon as it does.',
      );
    } on ApiError catch (e) {
      // A failed check is not a failed payment: the webhook and the sweep are
      // still behind it, so say so rather than implying the money is gone.
      if (mounted) {
        showApiMessage(
          context,
          '${e.message} Your payment is still being checked.',
        );
      }
    }
    _key.currentState?.reload();
  }
}

/// A settled bill takes no more money and needs no more decisions.
const _settledInvoice = {'PAID', 'WAIVED', 'CANCELLED'};

/// The office menu for one invoice, as `{action: label}`.
///
/// Hiding an option is courtesy — the API enforces every one again — but two
/// of these are more than courtesy. Cancelling comes off once money has
/// landed, because the server refuses until the payment is reversed and an
/// option that only ever errors teaches people to ignore errors. Waiving
/// comes off a settled bill for the same reason.
Map<String, String> invoiceMenu({
  required String status,
  required double paid,
  required double balance,
  required bool manageFees,
  required bool recordPayment,
  bool adjustInvoice = false,
}) {
  final settled = _settledInvoice.contains(status);
  return {
    if (manageFees && status == 'DRAFT') 'issue': 'Issue to payer',
    if (recordPayment && !settled && balance > 0) 'pay': 'Record a payment',
    if (recordPayment && paid > 0) 'reverse': 'Reverse a payment',
    // Not `settled`: a PAID bill is still adjustable — upwards, or down to
    // what was paid. Only the two decisions are closed, and the server says
    // the same.
    if (adjustInvoice && status != 'WAIVED' && status != 'CANCELLED')
      'adjust': 'Adjust the bill',
    if (manageFees && !settled) 'waive': 'Waive',
    if (manageFees && paid == 0 && status != 'CANCELLED')
      'cancel': 'Cancel invoice',
    'document': 'Print invoice',
  };
}

class _InvoiceCard extends StatelessWidget {
  const _InvoiceCard({
    required this.invoice,
    required this.onPay,
    required this.onAction,
    required this.session,
  });

  final Map<String, dynamic> invoice;
  final VoidCallback onPay;

  /// The office menu — issue, take money, reverse, waive, cancel, print. What
  /// appears is decided here, from this invoice's state and this user's
  /// permissions; the API enforces every one of them again.
  final void Function(String action) onAction;
  final Session session;

  /// Colour carries the meaning at a glance; the word still says which one.
  static Widget _statusBadge(String status) => switch (status) {
    'PAID' || 'WAIVED' => StatusBadge.success(status),
    'CANCELLED' => StatusBadge.info(status),
    _ => StatusBadge.warning(status.isEmpty ? 'OPEN' : status),
  };

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final status = invoice['status'] as String? ?? '';
    final balance = double.tryParse('${invoice['balance']}') ?? 0;
    final paid = double.tryParse('${invoice['amount_paid']}') ?? 0;
    final payable = !_settledInvoice.contains(status) && balance > 0;
    final manageFees = session.can('finance.manage_fees');
    final recordPayment = session.can('finance.record_payment');

    final menu = invoiceMenu(
      status: status,
      paid: paid,
      balance: balance,
      manageFees: manageFees,
      recordPayment: recordPayment,
      adjustInvoice: session.can('finance.adjust_invoice'),
    );

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    invoice['full_name'] as String? ?? '',
                    style: text.titleMedium,
                  ),
                ),
                _statusBadge(status),
                if (menu.isNotEmpty)
                  PopupMenuButton<String>(
                    onSelected: onAction,
                    itemBuilder: (context) => [
                      for (final entry in menu.entries)
                        PopupMenuItem(
                          value: entry.key,
                          child: Text(entry.value),
                        ),
                    ],
                  ),
              ],
            ),
            Text('${invoice['number']}', style: text.bodySmall),
            const SizedBox(height: 12),
            for (final line
                in (invoice['lines'] as List? ?? const [])
                    .cast<Map<String, dynamic>>())
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: Row(
                  children: [
                    Expanded(child: Text(line['description'] as String? ?? '')),
                    Text(naira(line['amount'])),
                  ],
                ),
              ),
            const Divider(),
            _Row(label: 'Total', value: naira(invoice['total'])),
            _Row(label: 'Paid', value: naira(invoice['amount_paid'])),
            _Row(label: 'Balance', value: naira(balance), bold: true),
            if (invoice['due_date'] != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  'Due ${invoice['due_date']}',
                  style: text.bodySmall,
                ),
              ),
            if (payable)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    onPressed: onPay,
                    icon: const Icon(Icons.payment),
                    label: Text('Pay ${naira(balance)}'),
                  ),
                ),
              ),
            // A draft is not a bill yet: it is invisible to the payer, and
            // saying so where the pay button would be saves the phone call.
            if (status == 'DRAFT')
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(
                  'Draft — the payer cannot see this until it is issued.',
                  style: text.bodySmall,
                ),
              ),
            if ((invoice['waiver_reason'] as String? ?? '').isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  'Waived: ${invoice['waiver_reason']}',
                  style: text.bodySmall,
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// Waive, cancel, reverse: three decisions that are kept on the record for
/// good and read back in an audit, so all three need their why typed in. One
/// dialog, because the only difference between them is the wording.
class _ReasonDialog extends StatefulWidget {
  const _ReasonDialog({
    required this.title,
    required this.blurb,
    required this.hint,
    required this.confirmLabel,
  });

  final String title;
  final String blurb;
  final String hint;
  final String confirmLabel;

  @override
  State<_ReasonDialog> createState() => _ReasonDialogState();
}

class _ReasonDialogState extends State<_ReasonDialog> {
  final _reason = TextEditingController();

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(widget.title),
    content: Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(widget.blurb),
        const SizedBox(height: 12),
        TextField(
          controller: _reason,
          autofocus: true,
          maxLines: 2,
          maxLength: 255,
          decoration: InputDecoration(
            labelText: 'Reason',
            hintText: widget.hint,
          ),
          onChanged: (_) => setState(() {}),
        ),
      ],
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Back'),
      ),
      FilledButton(
        onPressed: _reason.text.trim().isEmpty
            ? null
            : () => Navigator.pop(context, _reason.text.trim()),
        child: Text(widget.confirmLabel),
      ),
    ],
  );
}

/// What is wrong with a proposed adjustment, or null when nothing is.
///
/// Pure, and separate from the dialog, because the server enforces the same
/// two rules and a bursar should not have to send a request to find out. The
/// interesting one is the last: a bill cannot be adjusted below the money
/// already taken, because that is a refund and nothing here pays money out.
String? adjustmentProblem({
  required List<String> amounts,
  required String discount,
  required String reason,
  required double paid,
  List<String> descriptions = const [],
}) {
  if (reason.trim().isEmpty) return 'Say why this bill is changing.';
  if (descriptions.any((text) => text.trim().isEmpty)) {
    return 'Every line needs a description.';
  }
  var subtotal = 0.0;
  for (final raw in amounts) {
    final amount = double.tryParse(raw.trim());
    if (amount == null || amount < 0) return 'Every line needs an amount.';
    subtotal += amount;
  }
  final off = double.tryParse(discount.trim().isEmpty ? '0' : discount.trim());
  if (off == null || off < 0) return 'The discount must be a number.';
  // The server floors the total at zero, so a discount past the subtotal is
  // legal — it just cannot land under what has been paid.
  final total = subtotal - off < 0 ? 0.0 : subtotal - off;
  if (total < paid) {
    return '${naira(paid)} has already been paid; the bill cannot go below '
        'that. Reverse the payment first.';
  }
  return null;
}

/// `FeeCategory` on the server, in its order. What a line is charged *for*,
/// which is how the bursary reports the term — a rebate written as OTHER is a
/// rebate nobody can total.
const feeCategories = {
  'TUITION': 'Tuition',
  'DEVELOPMENT': 'Development levy',
  'EXAMINATION': 'Examination',
  'ACCEPTANCE': 'Acceptance',
  'HOSTEL': 'Hostel / accommodation',
  'TRANSPORT': 'Transport',
  'UNIFORM': 'Uniform and books',
  'PTA': 'PTA / association dues',
  'OTHER': 'Other',
};

/// One line while it is being edited. The `fee_item` breadcrumb rides along
/// untouched so a line that came off the price list still points back at it
/// after the bill is rewritten.
class _LineEdit {
  _LineEdit(Map<String, dynamic> line)
    : description = TextEditingController(
        text: line['description'] as String? ?? '',
      ),
      amount = TextEditingController(text: '${line['amount'] ?? ''}'),
      // A category the app has never heard of — one added to the server
      // ahead of this build — would leave the dropdown with a value that is
      // not among its items, which is an assertion, not a fallback. So an
      // unknown one reads as Other rather than crashing the bill.
      category = feeCategories.containsKey(line['category'])
          ? line['category'] as String
          : 'OTHER',
      feeItem = line['fee_item'] as String?;

  _LineEdit.blank()
    : description = TextEditingController(),
      amount = TextEditingController(),
      category = 'OTHER',
      feeItem = null;

  final TextEditingController description;
  final TextEditingController amount;
  String category;
  final String? feeItem;

  Map<String, dynamic> get payload => {
    'description': description.text.trim(),
    'amount': amount.text.trim(),
    'category': category,
    'fee_item': ?feeItem,
  };

  void dispose() {
    description.dispose();
    amount.dispose();
  }
}

/// Rewriting one family's bill. Opens on what the invoice already says,
/// because the whole line set is what gets sent back.
class _AdjustDialog extends StatefulWidget {
  const _AdjustDialog({required this.invoice});

  final Map<String, dynamic> invoice;

  @override
  State<_AdjustDialog> createState() => _AdjustDialogState();
}

class _AdjustDialogState extends State<_AdjustDialog> {
  late final List<_LineEdit> _lines = [
    for (final line
        in (widget.invoice['lines'] as List? ?? const [])
            .cast<Map<String, dynamic>>())
      _LineEdit(line),
  ];
  late final _discount = TextEditingController(
    text: '${widget.invoice['discount'] ?? '0.00'}',
  );
  final _reason = TextEditingController();
  late final double _paid =
      double.tryParse('${widget.invoice['amount_paid']}') ?? 0;

  @override
  void dispose() {
    for (final line in _lines) {
      line.dispose();
    }
    _discount.dispose();
    _reason.dispose();
    super.dispose();
  }

  String? get _problem => adjustmentProblem(
    amounts: [for (final line in _lines) line.amount.text],
    descriptions: [for (final line in _lines) line.description.text],
    discount: _discount.text,
    reason: _reason.text,
    paid: _paid,
  );

  double get _total {
    var subtotal = 0.0;
    for (final line in _lines) {
      subtotal += double.tryParse(line.amount.text.trim()) ?? 0;
    }
    final off = double.tryParse(_discount.text.trim()) ?? 0;
    return subtotal - off < 0 ? 0 : subtotal - off;
  }

  @override
  Widget build(BuildContext context) {
    final problem = _problem;
    return AlertDialog(
      title: Text('Adjust ${widget.invoice['number']}'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Two rows to a line: the money on top, what it is charged for
            // underneath. Three fields abreast is narrower than a phone.
            for (final line in _lines)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.end,
                      children: [
                        Expanded(
                          child: TextField(
                            controller: line.description,
                            decoration: const InputDecoration(
                              labelText: 'Item',
                            ),
                            onChanged: (_) => setState(() {}),
                          ),
                        ),
                        const SizedBox(width: 8),
                        SizedBox(
                          width: 104,
                          child: TextField(
                            controller: line.amount,
                            textAlign: TextAlign.end,
                            keyboardType: const TextInputType.numberWithOptions(
                              decimal: true,
                            ),
                            decoration: const InputDecoration(
                              labelText: 'Amount',
                            ),
                            onChanged: (_) => setState(() {}),
                          ),
                        ),
                      ],
                    ),
                    Row(
                      children: [
                        Expanded(
                          child: DropdownButtonFormField<String>(
                            initialValue: line.category,
                            isExpanded: true,
                            decoration: const InputDecoration(
                              labelText: 'Category',
                              isDense: true,
                            ),
                            items: [
                              for (final entry in feeCategories.entries)
                                DropdownMenuItem(
                                  value: entry.key,
                                  child: Text(entry.value),
                                ),
                            ],
                            onChanged: (value) => setState(
                              () => line.category = value ?? 'OTHER',
                            ),
                          ),
                        ),
                        IconButton(
                          tooltip: 'Remove this line',
                          icon: const Icon(Icons.remove_circle_outline),
                          onPressed: () => setState(() {
                            _lines.remove(line);
                            line.dispose();
                          }),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton.icon(
                onPressed: () => setState(() => _lines.add(_LineEdit.blank())),
                icon: const Icon(Icons.add),
                label: const Text('Add a line'),
              ),
            ),
            TextField(
              controller: _discount,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
              ),
              decoration: const InputDecoration(labelText: 'Discount'),
              onChanged: (_) => setState(() {}),
            ),
            const SizedBox(height: 12),
            _Row(label: 'New total', value: naira(_total), bold: true),
            if (_paid > 0) _Row(label: 'Already paid', value: naira(_paid)),
            TextField(
              controller: _reason,
              maxLines: 2,
              maxLength: 255,
              decoration: const InputDecoration(
                labelText: 'Reason',
                hintText: 'Second child — sibling rebate',
              ),
              onChanged: (_) => setState(() {}),
            ),
            if (problem != null && _reason.text.trim().isNotEmpty)
              Text(
                problem,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Back'),
        ),
        FilledButton(
          onPressed: problem != null
              ? null
              : () => Navigator.pop(context, {
                  'lines': [for (final line in _lines) line.payload],
                  'discount': _discount.text.trim(),
                  'reason': _reason.text.trim(),
                }),
          child: const Text('Adjust'),
        ),
      ],
    );
  }
}

/// Money taken at the counter. Defaults to the whole balance because that is
/// what most people pay, and part payment is a correction away.
class _CounterPaymentDialog extends StatefulWidget {
  const _CounterPaymentDialog({required this.balance});

  final double balance;

  @override
  State<_CounterPaymentDialog> createState() => _CounterPaymentDialogState();
}

class _CounterPaymentDialogState extends State<_CounterPaymentDialog> {
  static const _methods = {
    'CASH': 'Cash',
    'TRANSFER': 'Transfer',
    'POS': 'POS',
    'CHEQUE': 'Cheque',
  };

  late final _amount = TextEditingController(
    text: widget.balance.toStringAsFixed(2),
  );
  final _payer = TextEditingController();
  final _note = TextEditingController();
  String _method = 'CASH';

  @override
  void dispose() {
    _amount.dispose();
    _payer.dispose();
    _note.dispose();
    super.dispose();
  }

  /// The server is the authority — it refuses anything over the balance —
  /// but a bursar typing 50000 for 5000 should find that out before the
  /// parent has left the counter.
  String? get _problem {
    final amount = double.tryParse(_amount.text.trim());
    if (amount == null || amount <= 0) return 'Enter an amount.';
    if (amount > widget.balance) {
      return 'That is more than the ${naira(widget.balance)} outstanding.';
    }
    return null;
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Record a payment'),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: _amount,
            autofocus: true,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(
              labelText: 'Amount',
              helperText: '${naira(widget.balance)} outstanding',
              errorText: _amount.text.isEmpty ? null : _problem,
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            children: [
              for (final entry in _methods.entries)
                ChoiceChip(
                  label: Text(entry.value),
                  selected: _method == entry.key,
                  onSelected: (_) => setState(() => _method = entry.key),
                ),
            ],
          ),
          TextField(
            controller: _payer,
            decoration: const InputDecoration(
              labelText: 'Who paid',
              helperText: 'Optional — the name on the teller or the parent',
            ),
          ),
          TextField(
            controller: _note,
            decoration: const InputDecoration(labelText: 'Note'),
          ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: _problem != null
            ? null
            : () => Navigator.pop(context, {
                'amount': _amount.text.trim(),
                'method': _method,
                'payer_name': _payer.text.trim(),
                'note': _note.text.trim(),
              }),
        child: const Text('Record'),
      ),
    ],
  );
}

/// What a billing run did, including what it could not do. A run that skipped
/// four students has to say which four — the same report the roster import
/// shows, for the same reason.
class _BillingReportDialog extends StatelessWidget {
  const _BillingReportDialog({required this.report});

  final Map<String, dynamic> report;

  @override
  Widget build(BuildContext context) {
    final created = report['created'] ?? 0;
    final errors = (report['errors'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
    return AlertDialog(
      title: Text(errors.isEmpty ? 'Invoices raised' : 'Nothing was written'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              errors.isEmpty
                  ? '$created invoice${created == 1 ? '' : 's'} raised as '
                        'drafts. Issue them when the bursar is ready — a draft '
                        'is invisible to the payer.'
                  : 'The run was rolled back, so no invoice was created. Fix '
                        'these and run it again:',
            ),
            if (errors.isNotEmpty) const SizedBox(height: 12),
            for (final error in errors.take(20))
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: Text('• ${error['identifier']}: ${error['message']}'),
              ),
            if (errors.length > 20) Text('…and ${errors.length - 20} more.'),
          ],
        ),
      ),
      actions: [
        FilledButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Close'),
        ),
      ],
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.label, required this.value, this.bold = false});

  final String label;
  final String value;
  final bool bold;

  @override
  Widget build(BuildContext context) {
    final style = bold
        ? Theme.of(context).textTheme.titleMedium
        : Theme.of(context).textTheme.bodyMedium;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: [
          Expanded(child: Text(label, style: style)),
          Text(value, style: style),
        ],
      ),
    );
  }
}

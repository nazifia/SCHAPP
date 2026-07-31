import 'package:flutter/material.dart';

import '../api/api_error.dart';
import '../api/repository.dart';
import '../auth/session.dart';
import '../design/app_shell.dart';
import '../design/async_view.dart';
import 'fees_screen.dart' show feeCategories;

/// The price list: what a *cohort* is charged, edited in one place.
///
/// This is where a fee is meant to change. The adjustment dialog on the fees
/// screen rewrites one family's bill — a sibling rebate, a levy charged in
/// error — and a bursar re-pricing tuition through it would be typing the same
/// new number onto eight hundred invoices, one at a time, with eight hundred
/// chances to type it differently.
///
/// A price edited here reaches every *draft* invoice raised from the list and
/// stops at the issued ones: the server's `fee_changed` does that, and the
/// stopping is the point. A bill a parent is already holding does not move
/// under them.
///
/// Reads are open to anyone signed in — a parent may see what the term costs —
/// and every write needs `finance.manage_fees`, which the server enforces
/// again.
class FeeStructuresScreen extends StatefulWidget {
  const FeeStructuresScreen({super.key, required this.session});

  final Session session;

  @override
  State<FeeStructuresScreen> createState() => _FeeStructuresScreenState();
}

class _FeeStructuresScreenState extends State<FeeStructuresScreen> {
  final _key = GlobalKey<AsyncViewState<List<Map<String, dynamic>>>>();
  late final Repository _repo = Repository(widget.session.api);
  List<Map<String, dynamic>> _sessions = const [];
  List<Map<String, dynamic>> _terms = const [];
  List<Map<String, dynamic>> _levels = const [];

  /// The server's own `FeeCategory`, falling back to the constant compiled
  /// into the app. Both are checked against each other by a test on the
  /// server, so the fallback is never the wrong list — only, eventually, a
  /// short one.
  Map<String, String> _categories = feeCategories;

  bool _showRetired = false;

  bool get _canManage => widget.session.can('finance.manage_fees');

  @override
  void initState() {
    super.initState();
    _loadPickers();
  }

  /// Everything the forms pick from, in one go. A failure leaves the pickers
  /// empty rather than the screen broken: the item form needs none of them,
  /// and the structure form only needs a session.
  Future<void> _loadPickers() async {
    try {
      final lists = await Future.wait([
        _repo.sessions(),
        _repo.terms(),
        _repo.classLevels(),
        _repo.feeCategoryChoices(),
      ]);
      if (!mounted) return;
      setState(() {
        _sessions = lists[0];
        _terms = lists[1];
        _levels = lists[2];
        final choices = categoriesFrom(lists[3]);
        if (choices.isNotEmpty) _categories = choices;
      });
    } on ApiError {
      // Keep the compiled-in categories and offer no scope pickers.
    }
  }

  void _reload() => _key.currentState?.reload();

  String _nameIn(List<Map<String, dynamic>> rows, Object? id) {
    if (id == null) return '';
    for (final row in rows) {
      if (row['id'] == id) return (row['name'] ?? row['code'] ?? '').toString();
    }
    return '';
  }

  /// Who this price list bills, read back from the ids the row carries.
  String _scope(Map<String, dynamic> structure) {
    final session = _nameIn(_sessions, structure['session']);
    final term = _nameIn(_terms, structure['term']);
    final level = _nameIn(_levels, structure['level']);
    return [
      if (session.isNotEmpty) session,
      // No term means the charge covers the whole session and is billed once,
      // which is a real and common shape — acceptance fees, for one.
      term.isNotEmpty ? term : 'Whole session',
      level.isNotEmpty ? level : 'Every level',
    ].join(' · ');
  }

  Future<void> _editStructure([Map<String, dynamic>? structure]) async {
    final changed = await showDialog<bool>(
      context: context,
      builder: (context) => _StructureDialog(
        repo: _repo,
        sessions: _sessions,
        terms: _terms,
        levels: _levels,
        structure: structure,
      ),
    );
    if (changed == true) _reload();
  }

  Future<void> _editItem(
    Map<String, dynamic> structure, [
    Map<String, dynamic>? item,
  ]) async {
    final changed = await showDialog<bool>(
      context: context,
      builder: (context) => _ItemDialog(
        repo: _repo,
        categories: _categories,
        structureId: structure['id'] as String,
        nextOrder: (structure['items'] as List? ?? const []).length + 1,
        item: item,
      ),
    );
    if (changed == true) _reload();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Fees & price lists'),
      actions: [
        // Retired lists are off by default: what a school works from is what
        // it currently charges. They are kept, never deleted, because last
        // term's invoices were raised from them.
        IconButton(
          tooltip: _showRetired ? 'Hide retired' : 'Show retired',
          icon: Icon(
            _showRetired ? Icons.visibility_off_outlined : Icons.history,
          ),
          onPressed: () {
            setState(() => _showRetired = !_showRetired);
            _reload();
          },
        ),
      ],
    ),
    floatingActionButton: _canManage
        ? FloatingActionButton.extended(
            onPressed: () => _editStructure(),
            icon: const Icon(Icons.add),
            label: const Text('New price list'),
          )
        : null,
    body: AsyncView<List<Map<String, dynamic>>>(
      key: _key,
      load: () => _repo.feeStructures(active: _showRetired ? null : true),
      empty: EmptyState(
        icon: Icons.price_change_outlined,
        message: _canManage
            ? 'No price list yet. Add one, put the term\'s charges on it, '
                  'then raise the invoices from the Fees screen.'
            : 'No price list has been published yet.',
      ),
      builder: (context, structures) => ListView(
        padding: const EdgeInsets.only(bottom: 88),
        children: [
          if (_canManage)
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 12, 16, 4),
              child: Text(
                'Changing a price here re-prices every draft invoice raised '
                'from the list. Bills already issued keep the price they were '
                'issued at — adjust those one at a time from Fees.',
              ),
            ),
          for (final structure in structures)
            _StructureTile(
              structure: structure,
              scope: _scope(structure),
              categories: _categories,
              canManage: _canManage,
              onEdit: () => _editStructure(structure),
              onEditItem: (item) => _editItem(structure, item),
              onAddItem: () => _editItem(structure),
            ),
        ],
      ),
    ),
  );
}

/// The server's choice list as the picker wants it. Empty in, empty out — the
/// caller keeps its fallback rather than showing a dropdown with nothing in it.
Map<String, String> categoriesFrom(List<Map<String, dynamic>> rows) => {
  for (final row in rows)
    if (row['value'] != null)
      '${row['value']}': '${row['label'] ?? row['value']}',
};

/// The write body for a price list. `session` is the only required scope;
/// a null term bills once for the year, a null level bills every year group.
Map<String, dynamic> feeStructurePayload({
  required String name,
  required String session,
  String? term,
  String? level,
  String dueInDays = '',
  bool isActive = true,
}) => {
  'name': name.trim(),
  'session': session,
  'term': term,
  'level': level,
  // The model's own default, so a blank box is not a validation error at the
  // counter. Three weeks is the usual grace on a Nigerian term bill.
  'due_in_days': int.tryParse(dueInDays.trim()) ?? 21,
  'is_active': isActive,
};

/// The write body for one line of a price list.
Map<String, dynamic> feeItemPayload({
  required String structure,
  required String name,
  required String amount,
  String category = 'OTHER',
  bool isOptional = false,
  int order = 1,
}) => {
  'structure': structure,
  'name': name.trim(),
  'amount': amount.trim(),
  'category': category,
  'is_optional': isOptional,
  'order': order,
};

/// What is wrong with a proposed line, or null when nothing is.
///
/// Pure and separate from the dialog for the same reason `adjustmentProblem`
/// is: the server enforces both rules, and a bursar should not have to send a
/// request to be told a fee needs a name.
String? feeItemProblem({required String name, required String amount}) {
  if (name.trim().isEmpty) return 'Give the charge a name.';
  final value = double.tryParse(amount.trim());
  if (value == null) return 'The amount must be a number.';
  // Zero is allowed and occasionally meant — a line kept on the bill for the
  // year it is not charged — but a negative fee is money owed *to* a parent,
  // and nothing here pays money out.
  if (value < 0) return 'The amount cannot be negative.';
  return null;
}

/// One price list, opened to show what is on it. The total under the name is
/// the compulsory items only — the optional ones are charged per student, so
/// no cohort is billed that number.
class _StructureTile extends StatelessWidget {
  const _StructureTile({
    required this.structure,
    required this.scope,
    required this.categories,
    required this.canManage,
    required this.onEdit,
    required this.onEditItem,
    required this.onAddItem,
  });

  final Map<String, dynamic> structure;
  final String scope;
  final Map<String, String> categories;
  final bool canManage;
  final VoidCallback onEdit;
  final void Function(Map<String, dynamic> item) onEditItem;
  final VoidCallback onAddItem;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final items = (structure['items'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
    final retired = structure['is_active'] == false;

    return Card(
      margin: const EdgeInsets.fromLTRB(12, 6, 12, 6),
      child: ExpansionTile(
        title: Row(
          children: [
            Expanded(
              child: Text(
                structure['name'] as String? ?? '',
                style: text.titleMedium,
              ),
            ),
            if (retired)
              const Padding(
                padding: EdgeInsets.only(left: 8),
                child: Chip(
                  label: Text('Retired'),
                  visualDensity: VisualDensity.compact,
                ),
              ),
          ],
        ),
        subtitle: Text('$scope · ${naira(structure['total'])}'),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 8, 8),
        children: [
          if (items.isEmpty)
            const ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text('Nothing on this list yet.'),
            ),
          for (final item in items)
            ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(item['name'] as String? ?? ''),
              subtitle: Text(
                [
                  categories[item['category']] ?? '${item['category']}',
                  // The one distinction that changes what a cohort is billed:
                  // an optional line is charged only to the students who take
                  // it, so it is outside the total above.
                  if (item['is_optional'] == true) 'Optional',
                ].join(' · '),
              ),
              trailing: Text(naira(item['amount'])),
              onTap: canManage ? () => onEditItem(item) : null,
            ),
          if (canManage)
            Row(
              children: [
                TextButton.icon(
                  onPressed: onAddItem,
                  icon: const Icon(Icons.add),
                  label: const Text('Add a charge'),
                ),
                const Spacer(),
                TextButton.icon(
                  onPressed: onEdit,
                  icon: const Icon(Icons.tune),
                  label: const Text('Price list'),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

/// One dialog for both jobs: `structure` null adds, otherwise it edits.
class _StructureDialog extends StatefulWidget {
  const _StructureDialog({
    required this.repo,
    required this.sessions,
    required this.terms,
    required this.levels,
    this.structure,
  });

  final Repository repo;
  final List<Map<String, dynamic>> sessions;
  final List<Map<String, dynamic>> terms;
  final List<Map<String, dynamic>> levels;
  final Map<String, dynamic>? structure;

  @override
  State<_StructureDialog> createState() => _StructureDialogState();
}

class _StructureDialogState extends State<_StructureDialog> {
  late final _name = TextEditingController(
    text: widget.structure?['name'] as String? ?? '',
  );
  late final _dueInDays = TextEditingController(
    text: '${widget.structure?['due_in_days'] ?? 21}',
  );
  late String? _session =
      widget.structure?['session'] as String? ??
      // The year the school is in, which is what a new list is nearly always
      // for. Falls back to the first row when no session is flagged current.
      (widget.sessions.isEmpty
          ? null
          : (widget.sessions.firstWhere(
                  (row) => row['is_current'] == true,
                  orElse: () => widget.sessions.first,
                )['id']
                as String));
  late String? _term = widget.structure?['term'] as String?;
  late String? _level = widget.structure?['level'] as String?;
  late bool _active = widget.structure?['is_active'] != false;
  bool _saving = false;

  bool get _isNew => widget.structure == null;

  /// Only the terms of the chosen session: offering last year's second term
  /// as the scope of this year's bill is a mis-scoped billing run nobody
  /// notices until the invoices are out.
  List<Map<String, dynamic>> get _termsInSession => [
    for (final term in widget.terms)
      if (_session == null || term['session'] == _session) term,
  ];

  @override
  void dispose() {
    _name.dispose();
    _dueInDays.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_name.text.trim().isEmpty || _session == null) {
      showApiMessage(context, 'A name and a session are required.');
      return;
    }
    final payload = feeStructurePayload(
      name: _name.text,
      session: _session!,
      term: _term,
      level: _level,
      dueInDays: _dueInDays.text,
      isActive: _active,
    );

    setState(() => _saving = true);
    try {
      if (_isNew) {
        await widget.repo.createFeeStructure(payload);
      } else {
        await widget.repo.updateFeeStructure(
          widget.structure!['id'] as String,
          payload,
        );
      }
      if (mounted) Navigator.pop(context, true);
    } on ApiError catch (e) {
      // The common one is the scope clash — one list of this name for this
      // session, term and level already exists — and the server names it.
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _delete() async {
    final deleted = await confirmDelete(
      context,
      title: 'Delete this price list?',
      message:
          'Its charges go with it. Invoices already raised keep their lines '
          '— they were snapshotted — but lose the link back to the list.\n\n'
          'If it has ever been billed from, untick "In use" instead: that '
          'retires it without unpicking last term.',
      onConfirm: () =>
          widget.repo.deleteFeeStructure(widget.structure!['id'] as String),
    );
    if (deleted && mounted) Navigator.pop(context, true);
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(_isNew ? 'New price list' : 'Price list'),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: _name,
            decoration: const InputDecoration(
              labelText: 'Name',
              helperText: 'First Term fees, JSS boarding, Acceptance',
            ),
            textInputAction: TextInputAction.next,
          ),
          const SizedBox(height: 8),
          DropdownButtonFormField<String?>(
            initialValue: _session,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Session'),
            items: [
              for (final session in widget.sessions)
                DropdownMenuItem(
                  value: session['id'] as String,
                  child: Text('${session['name']}'),
                ),
            ],
            onChanged: (value) => setState(() {
              _session = value;
              // The term belonged to the old session; keeping it would scope
              // the list to a term of a different year.
              _term = null;
            }),
          ),
          const SizedBox(height: 8),
          DropdownButtonFormField<String?>(
            initialValue: _term,
            isExpanded: true,
            decoration: const InputDecoration(
              labelText: 'Term',
              helperText: 'Blank charges once for the whole session',
            ),
            items: [
              const DropdownMenuItem(value: null, child: Text('Whole session')),
              for (final term in _termsInSession)
                DropdownMenuItem(
                  value: term['id'] as String,
                  child: Text('${term['name']}'),
                ),
            ],
            onChanged: (value) => setState(() => _term = value),
          ),
          const SizedBox(height: 8),
          DropdownButtonFormField<String?>(
            initialValue: _level,
            isExpanded: true,
            decoration: const InputDecoration(
              labelText: 'Level',
              helperText: 'Blank bills every year group the same',
            ),
            items: [
              const DropdownMenuItem(value: null, child: Text('Every level')),
              for (final level in widget.levels)
                DropdownMenuItem(
                  value: level['id'] as String,
                  child: Text('${level['name'] ?? level['code']}'),
                ),
            ],
            onChanged: (value) => setState(() => _level = value),
          ),
          TextField(
            controller: _dueInDays,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(
              labelText: 'Due after (days)',
              helperText: 'Counted from the day the bill is issued',
            ),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('In use'),
            subtitle: const Text('Off retires it without touching old bills'),
            value: _active,
            onChanged: (value) => setState(() => _active = value),
          ),
        ],
      ),
    ),
    actions: [
      if (!_isNew)
        TextButton.icon(
          onPressed: _saving ? null : _delete,
          icon: const Icon(Icons.delete_outline),
          label: const Text('Delete'),
        ),
      TextButton(
        onPressed: _saving ? null : () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: _saving ? null : _save,
        child: Text(_isNew ? 'Add' : 'Save'),
      ),
    ],
  );
}

/// One charge on a price list. This is the dialog that re-prices a cohort: the
/// server carries the new amount into every draft invoice raised from the list.
class _ItemDialog extends StatefulWidget {
  const _ItemDialog({
    required this.repo,
    required this.categories,
    required this.structureId,
    required this.nextOrder,
    this.item,
  });

  final Repository repo;
  final Map<String, String> categories;
  final String structureId;
  final int nextOrder;
  final Map<String, dynamic>? item;

  @override
  State<_ItemDialog> createState() => _ItemDialogState();
}

class _ItemDialogState extends State<_ItemDialog> {
  late final _name = TextEditingController(
    text: widget.item?['name'] as String? ?? '',
  );
  late final _amount = TextEditingController(text: '${widget.item?['amount'] ?? ''}');
  // A category the app has never heard of — one added to the server ahead of
  // this build — is not among the dropdown's items, which is an assertion
  // rather than a fallback. So an unknown one reads as Other.
  late String _category = widget.categories.containsKey(widget.item?['category'])
      ? widget.item!['category'] as String
      : 'OTHER';
  late bool _optional = widget.item?['is_optional'] == true;
  bool _saving = false;

  bool get _isNew => widget.item == null;

  @override
  void dispose() {
    _name.dispose();
    _amount.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final problem = feeItemProblem(name: _name.text, amount: _amount.text);
    if (problem != null) {
      showApiMessage(context, problem);
      return;
    }
    final payload = feeItemPayload(
      structure: widget.structureId,
      name: _name.text,
      amount: _amount.text,
      category: _category,
      isOptional: _optional,
      order: (widget.item?['order'] as int?) ?? widget.nextOrder,
    );

    setState(() => _saving = true);
    try {
      if (_isNew) {
        await widget.repo.createFeeItem(payload);
      } else {
        await widget.repo.updateFeeItem(widget.item!['id'] as String, payload);
      }
      if (mounted) Navigator.pop(context, true);
    } on ApiError catch (e) {
      if (mounted) showApiMessage(context, e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _delete() async {
    final deleted = await confirmDelete(
      context,
      title: 'Remove this charge?',
      message:
          'It comes off the price list and off every draft invoice raised '
          'from it. Invoices already issued keep the line.',
      onConfirm: () => widget.repo.deleteFeeItem(widget.item!['id'] as String),
    );
    if (deleted && mounted) Navigator.pop(context, true);
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(_isNew ? 'Add a charge' : 'Edit charge'),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: _name,
            autofocus: true,
            decoration: const InputDecoration(
              labelText: 'Charge',
              helperText: 'Tuition, Development levy, Bus',
            ),
            textInputAction: TextInputAction.next,
            onChanged: (_) => setState(() {}),
          ),
          TextField(
            controller: _amount,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(labelText: 'Amount'),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 8),
          DropdownButtonFormField<String>(
            initialValue: _category,
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Category'),
            items: [
              for (final entry in widget.categories.entries)
                DropdownMenuItem(value: entry.key, child: Text(entry.value)),
            ],
            onChanged: (value) => setState(() => _category = value ?? 'OTHER'),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Optional'),
            subtitle: const Text(
              'Hostel, bus, lunch — charged only to the students who take it, '
              'and left out of the cohort total',
            ),
            value: _optional,
            onChanged: (value) => setState(() => _optional = value),
          ),
          if (!_isNew)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Text(
                'Saving a new amount re-prices every draft invoice raised '
                'from this list. Issued bills are left alone.',
              ),
            ),
        ],
      ),
    ),
    actions: [
      if (!_isNew)
        TextButton.icon(
          onPressed: _saving ? null : _delete,
          icon: const Icon(Icons.delete_outline),
          label: const Text('Remove'),
        ),
      TextButton(
        onPressed: _saving ? null : () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed:
            _saving ||
                feeItemProblem(name: _name.text, amount: _amount.text) != null
            ? null
            : _save,
        child: Text(_isNew ? 'Add' : 'Save'),
      ),
    ],
  );
}

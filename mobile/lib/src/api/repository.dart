import 'dart:typed_data';

import 'api_error.dart';
import 'client.dart';

/// The server's `FileExtensionValidator` list and its size cap, mirrored so
/// the picker can refuse a file before it is uploaded rather than after. Keep
/// them in step with `apps/people/models.py`.
const documentExtensions = ['pdf', 'jpg', 'jpeg', 'png', 'webp', 'heic'];
const maxDocumentBytes = 10 * 1024 * 1024;

/// Every endpoint the feature screens use, in one place.
///
/// Not a generated client and not one repository per module: it is a thin
/// naming layer over [ApiClient] so a screen says `repo.markSheet(...)`
/// instead of assembling a path, and so a change of URL is one edit. State
/// still lives in the screens — there is no cache layer here, because
/// [ApiClient] already reads through the offline store.
class Repository {
  const Repository(this.api);

  final ApiClient api;

  /// Cursor pagination puts rows under `results`; a bare list also happens
  /// (actions that return one, like `sheet`). Both read the same way here.
  static List<Map<String, dynamic>> rows(dynamic data) {
    if (data is List) return data.cast<Map<String, dynamic>>();
    if (data is Map && data['results'] is List) {
      return (data['results'] as List).cast<Map<String, dynamic>>();
    }
    return const [];
  }

  /// The server caps a page at 200 rows, so ten pages is two thousand — past
  /// any real class list, register or approval queue, and a bound on a `next`
  /// link that points at itself.
  static const _maxPages = 10;

  /// Only the cursor is taken from `next`: the server writes it as an absolute
  /// URL off the request host, which behind a proxy — or a Docker container
  /// answering `localhost:8000` to a browser on another port — is not a URL
  /// this device can reach.
  static String? _cursorIn(dynamic next) =>
      next is String ? Uri.tryParse(next)?.queryParameters['cursor'] : null;

  /// Every page, not the first one.
  ///
  /// A screen asks for a list and gets a list; nothing above this layer knows
  /// about cursors. Asking for `page_size: 200` and stopping there was a silent
  /// truncation exactly where it hurts — an approval queue or a class list that
  /// looks complete and is not, so the rows past the cap are ones nobody ever
  /// sees to act on.
  ///
  /// An unpaginated endpoint answers a bare list, which has no `next`, so this
  /// is one request there as before.
  Future<List<Map<String, dynamic>>> _list(
    String path, {
    Map<String, dynamic>? query,
  }) async {
    final all = <Map<String, dynamic>>[];
    String? cursor;
    for (var page = 0; page < _maxPages; page++) {
      final params = {...?query, 'cursor': ?cursor};
      dynamic data;
      try {
        data = (await api.get(
          path,
          query: params.isEmpty ? null : params,
        )).data;
      } on ApiError catch (error) {
        // Offline, and this page was never cached: the pages already read came
        // off the device and are worth more than the error. The first page is
        // not caught — a screen with nothing to show must say so.
        if (page == 0 || !error.isOffline) rethrow;
        break;
      }
      all.addAll(rows(data));
      cursor = data is Map ? _cursorIn(data['next']) : null;
      if (cursor == null) break;
    }
    return all;
  }

  /// A create or an edit against a plain viewset, which answer the same shape.
  Future<Map<String, dynamic>> _write(
    String method,
    String path,
    Map<String, dynamic> fields,
  ) async {
    final response = method == 'POST'
        ? await api.post(path, body: fields)
        : await api.patch(path, body: fields);
    return (response.data as Map).cast<String, dynamic>();
  }

  // --- academics -----------------------------------------------------------

  Future<List<Map<String, dynamic>>> sessions() =>
      _list('/academics/sessions/', query: {'page_size': '50'});

  /// Every term, or one session's. Unfiltered runs to three rows a year and
  /// the calendar screen wants only the year the school is in.
  Future<List<Map<String, dynamic>>> terms({String? session}) => _list(
    '/academics/terms/',
    query: {'page_size': '20', 'session': ?session},
  );

  Future<Map<String, dynamic>> createSession(Map<String, dynamic> fields) =>
      _write('POST', '/academics/sessions/', fields);

  Future<Map<String, dynamic>> updateSession(
    String id,
    Map<String, dynamic> fields,
  ) => _write('PATCH', '/academics/sessions/$id/', fields);

  Future<Map<String, dynamic>> createTerm(Map<String, dynamic> fields) =>
      _write('POST', '/academics/terms/', fields);

  Future<Map<String, dynamic>> updateTerm(
    String id,
    Map<String, dynamic> fields,
  ) => _write('PATCH', '/academics/terms/$id/', fields);

  /// Roll the school over to a session. The server picks the term inside it by
  /// the calendar, so the two halves of "now" cannot disagree.
  ///
  /// Never queued. An outbox holds a write until the phone is back, and this
  /// one flips the whole school's year — replayed a week later it would drag
  /// everybody back into the term they have since left.
  Future<Map<String, dynamic>> setCurrentSession(String id) async {
    final response = await api.post('/academics/sessions/$id/set-current/');
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Advance to a term — first to second, and so on. Promotes the term's
  /// session too, so this is also how a school enters a new year's first term.
  /// Never queued, for the reason above.
  Future<Map<String, dynamic>> setCurrentTerm(String id) async {
    final response = await api.post('/academics/terms/$id/set-current/');
    return (response.data as Map).cast<String, dynamic>();
  }

  /// The teacher's home screen in one request: current term, the arms they
  /// teach, the subjects they teach. Saves three round trips and any guessing
  /// about which filters a teacher is allowed to apply.
  Future<Map<String, dynamic>> myClasses() async {
    final response = await api.get('/academics/my-classes/');
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Arms carry `enrolled` and `seats_left` alongside `capacity`, counted
  /// against the current session — that is what makes it possible to pick a
  /// class that has room instead of finding out from the refusal.
  Future<List<Map<String, dynamic>>> classArms() =>
      _list('/academics/arms/', query: {'page_size': '100'});

  Future<Map<String, dynamic>> createClassArm(
    Map<String, dynamic> fields,
  ) async {
    final response = await api.post('/academics/arms/', body: fields);
    return (response.data as Map).cast<String, dynamic>();
  }

  Future<Map<String, dynamic>> updateClassArm(
    String id,
    Map<String, dynamic> fields,
  ) async {
    final response = await api.patch('/academics/arms/$id/', body: fields);
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Refused with 409 `DEPENDENTS_EXIST` once anybody has been seated in it,
  /// because the enrolments would cascade and take a term's register with
  /// them. Untick "In use" for a class the school has stopped running.
  Future<void> deleteClassArm(String id) => api.delete('/academics/arms/$id/');

  Future<List<Map<String, dynamic>>> classLevels() =>
      _list('/academics/levels/', query: {'page_size': '50'});

  Future<Map<String, dynamic>> createClassLevel(Map<String, dynamic> fields) =>
      _write('POST', '/academics/levels/', fields);

  Future<Map<String, dynamic>> updateClassLevel(
    String id,
    Map<String, dynamic> fields,
  ) => _write('PATCH', '/academics/levels/$id/', fields);

  /// Refused with 409 `DEPENDENTS_EXIST` once anything hangs off it, and by
  /// then almost everything does: the arms cascade, and an enrolment protects
  /// the level outright.
  Future<void> deleteClassLevel(String id) =>
      api.delete('/academics/levels/$id/');

  /// Science / Arts / Commercial. Empty at a school that streams nobody, and
  /// at every tertiary institution — which is what the subject form reads to
  /// decide whether to offer the field at all.
  Future<List<Map<String, dynamic>>> streams() =>
      _list('/academics/streams/', query: {'page_size': '50'});

  Future<Map<String, dynamic>> createStream(Map<String, dynamic> fields) =>
      _write('POST', '/academics/streams/', fields);

  Future<Map<String, dynamic>> updateStream(
    String id,
    Map<String, dynamic> fields,
  ) => _write('PATCH', '/academics/streams/$id/', fields);

  Future<void> deleteStream(String id) => api.delete('/academics/streams/$id/');

  /// Tertiary only, and empty at a secondary school for the same reason.
  Future<List<Map<String, dynamic>>> departments() =>
      _list('/academics/departments/', query: {'page_size': '100'});

  /// The register for one class, in roll-number order, unnumbered pupils last.
  /// Enrolments, not students: the roll number and the class both live there.
  Future<List<Map<String, dynamic>>> classRoster(String armId) =>
      _list('/academics/arms/$armId/students/');

  /// Active students with no class this session. After a promotion run that
  /// is the entire school — `apply_promotions` moves everyone up deliberately
  /// unclassed, because guessing an arm would put a pupil in a stream they
  /// never chose.
  Future<List<Map<String, dynamic>>> unplacedEnrolments({String? level}) =>
      _list(
        '/academics/enrolments/unplaced/',
        query: {'level': ?level, 'page_size': '200'},
      );

  /// Seat a batch in one class. All or nothing: the server checks the whole
  /// batch against the seats before it writes any of it, so a class with room
  /// for two refuses three rather than seating two and reporting a failure.
  ///
  /// Moving one pupil is this call with one id — a transfer and a first
  /// placement are the same operation on the server, and going through the
  /// batch endpoint is what gives a single move the capacity override too.
  ///
  /// Never queued. The outbox replays by path and body, and a replay would
  /// re-seat pupils who have since been moved somewhere else.
  Future<List<Map<String, dynamic>>> allocateToArm(
    String armId, {
    required List<String> enrolments,
    bool enforceCapacity = true,
  }) async {
    final response = await api.post(
      '/academics/arms/$armId/allocate/',
      body: {'enrolments': enrolments, 'enforce_capacity': enforceCapacity},
    );
    return rows(response.data);
  }

  /// The subject/course catalogue. `search` is server-side over code and
  /// title, the same way the staff list works — a tertiary catalogue runs to
  /// several hundred courses and the client holds one page of them.
  ///
  /// `forEnrolment` narrows it to what one student may actually take — their
  /// level and below, their programme's department, their stream — and `term`
  /// to the semester it is offered in. The server owns those rules because it
  /// is the one that enforces them at registration.
  Future<List<Map<String, dynamic>>> subjects({
    String? level,
    String? category,
    bool? active,
    String? search,
    String? forEnrolment,
    String? term,
  }) => _list(
    '/academics/subjects/',
    query: {
      'page_size': '200',
      'level': ?level,
      'category': ?category,
      'is_active': ?active?.toString(),
      'search': ?search,
      'for_enrolment': ?forEnrolment,
      'term': ?term,
    },
  );

  Future<Map<String, dynamic>> createSubject(
    Map<String, dynamic> fields,
  ) async {
    final response = await api.post('/academics/subjects/', body: fields);
    return (response.data as Map).cast<String, dynamic>();
  }

  Future<Map<String, dynamic>> updateSubject(
    String id,
    Map<String, dynamic> fields,
  ) async {
    final response = await api.patch('/academics/subjects/$id/', body: fields);
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Hard, and refused with 409 `DEPENDENTS_EXIST` the moment a registration,
  /// a mark or a timetable period points at it. That is the intended shape:
  /// deleting is for the code keyed wrong this morning, and `is_active: false`
  /// is what "we no longer offer this" means once a term has been taught.
  Future<void> deleteSubject(String id) =>
      api.delete('/academics/subjects/$id/');

  /// One student's cohort memberships. The registration screen has a pupil and
  /// a term and needs the enrolment that joins them, which is the row every
  /// registration hangs off.
  Future<List<Map<String, dynamic>>> enrolments({
    String? student,
    String? session,
    String? status,
  }) => _list(
    '/academics/enrolments/',
    query: {
      'page_size': '50',
      'student': ?student,
      'session': ?session,
      'status': ?status,
    },
  );

  /// Register a whole term's courses in one call.
  ///
  /// All or nothing on purpose: the server checks every course — prerequisite,
  /// credit ceiling, registration window — before it writes any of them, and
  /// refuses the whole set with `REGISTRATION_REJECTED` and a row per failure
  /// rather than leaving a half-registered semester behind.
  Future<List<Map<String, dynamic>>> registerSubjects({
    required String enrolment,
    required String term,
    required List<String> subjects,
    bool submit = true,
  }) async {
    final response = await api.post(
      '/academics/registrations/register/',
      body: {
        'enrolment': enrolment,
        'term': term,
        'subjects': subjects,
        'submit': submit,
      },
    );
    return rows(response.data);
  }

  /// Course registrations awaiting a decision. `status` is one of DRAFT,
  /// SUBMITTED, ADVISER_APPROVED, APPROVED, REJECTED, DROPPED — the queue
  /// screen asks for the two that are still open.
  Future<List<Map<String, dynamic>>> registrations({
    String? term,
    String? status,
    String? enrolment,
  }) => _list(
    '/academics/registrations/',
    query: {
      'page_size': '200',
      'term': ?term,
      'status': ?status,
      'enrolment': ?enrolment,
    },
  );

  /// Two steps, one endpoint: an adviser approves, then the HOD approves with
  /// `asHod` and that is what makes it final. `ignoreMinimum` waives the
  /// credit floor for the final-year student with twelve units left.
  Future<Map<String, dynamic>> approveRegistration(
    String id, {
    bool asHod = false,
    bool ignoreMinimum = false,
  }) async {
    final response = await api.post(
      '/academics/registrations/$id/approve/',
      body: {'as_hod': asHod, 'ignore_minimum': ignoreMinimum},
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  /// The reason is shown to the student, so it is required, not optional.
  Future<Map<String, dynamic>> rejectRegistration(
    String id, {
    required String reason,
  }) async {
    final response = await api.post(
      '/academics/registrations/$id/reject/',
      body: {'reason': reason},
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Undo a refusal — back to SUBMITTED, reason cleared. The way out of a
  /// wrong "no": re-registering is the student's, and only works while the
  /// add/drop window is open.
  Future<Map<String, dynamic>> reopenRegistration(String id) async {
    final response = await api.post('/academics/registrations/$id/reopen/');
    return (response.data as Map).cast<String, dynamic>();
  }

  /// The student's own withdrawal, as opposed to a refusal.
  Future<Map<String, dynamic>> dropRegistration(String id) async {
    final response = await api.post('/academics/registrations/$id/drop/');
    return (response.data as Map).cast<String, dynamic>();
  }

  // --- people --------------------------------------------------------------

  Future<List<Map<String, dynamic>>> students({
    String? classArm,
    String? status,
    String? search,
  }) => _list(
    '/people/students/',
    query: {
      'page_size': '200',
      'current_arm': ?classArm,
      'status': ?status,
      'search': ?search,
    },
  );

  /// One record, uncached: the register screen opens this to edit it, and an
  /// edit form filled from a stale page writes back stale fields.
  Future<Map<String, dynamic>> student(String id) async {
    final response = await api.get('/people/students/$id/', cache: false);
    return (response.data as Map).cast<String, dynamic>();
  }

  /// The admission number is issued by the server from the school's own
  /// numbering format — it is read-only here, and posting one is ignored.
  Future<Map<String, dynamic>> createStudent(
    Map<String, dynamic> fields,
  ) async {
    final response = await api.post('/people/students/', body: fields);
    return (response.data as Map).cast<String, dynamic>();
  }

  Future<Map<String, dynamic>> updateStudent(
    String id,
    Map<String, dynamic> fields,
  ) async {
    final response = await api.patch('/people/students/$id/', body: fields);
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Replace the passport photo on a record that already exists. Multipart,
  /// so it is its own call rather than a field on [updateStudent].
  Future<Map<String, dynamic>> uploadStudentPhoto(
    String id, {
    required String filename,
    required Uint8List bytes,
  }) async {
    final response = await api.upload(
      '/people/students/$id/',
      field: 'photo',
      filename: filename,
      bytes: bytes,
      method: 'PATCH',
    );
    return (response.data as Map?)?.cast<String, dynamic>() ?? const {};
  }

  /// Attach a guardian to a student. The server matches on phone, so the
  /// second child's form finds the same parent rather than making a twin —
  /// which is what "one login, several wards" rests on. Naming a primary
  /// demotes whoever held it.
  Future<Map<String, dynamic>> linkGuardian(
    String studentId, {
    required String phone,
    String firstName = '',
    String lastName = '',
    String relationship = 'GUARDIAN',
    bool isPrimary = false,
  }) async {
    final response = await api.post(
      '/people/students/$studentId/guardians/',
      body: {
        'phone': phone,
        'first_name': firstName,
        'last_name': lastName,
        'relationship': relationship,
        'is_primary': isPrimary,
      },
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Detach a guardian. The link id, not the guardian id: a parent with three
  /// children in the school is one record and three links, and this removes
  /// one of them. The server checks the link belongs to this student.
  Future<void> unlinkGuardian(String studentId, String linkId) =>
      api.delete('/people/students/$studentId/guardians/$linkId/');

  /// Upload a roster CSV. Returns the row report — `{created, updated,
  /// errors: [{index, identifier, code, message}]}` — for both outcomes: a
  /// file with a bad row wrote nothing and is answered with 422, which is a
  /// result to render, not an error to throw.
  ///
  /// `session` enrols the intake into that cohort as it lands; without it the
  /// students exist but sit in no class, which is what importing alumni wants.
  Future<Map<String, dynamic>> importStudents({
    required Uint8List file,
    required String filename,
    String? session,
    bool enforceCapacity = true,
  }) async {
    final response = await api.upload(
      '/people/students/import/',
      field: 'file',
      filename: filename,
      bytes: file,
      fields: {'enforce_capacity': '$enforceCapacity', 'session': ?session},
    );
    return (response.data as Map?)?.cast<String, dynamic>() ?? const {};
  }

  /// ID cards as one PDF, one page per student, each with its own signed QR.
  /// The server caps a batch at 400 pages.
  Future<Uint8List> idCards({String? classArm}) => api.download(
    '/people/students/id-cards/',
    query: {'current_arm': ?classArm},
  );

  /// Soft on the server: the record drops out of every list while its
  /// enrolments, results, attendance and guardian links stay exactly where
  /// they are. Nothing here can hard-delete a pupil, and nothing should.
  Future<void> deleteStudent(String id) => api.delete('/people/students/$id/');

  // --- student documents ----------------------------------------------------

  Future<List<Map<String, dynamic>>> studentDocuments(String studentId) =>
      _list('/people/documents/', query: {'student': studentId});

  /// Birth certificate, testimonial, O-level result. The server refuses
  /// anything outside [documentExtensions] or over [maxDocumentBytes], which
  /// is why both are checked here first — a 10 MB upload that will be refused
  /// is still 10 MB of somebody's data bundle.
  Future<Map<String, dynamic>> uploadStudentDocument({
    required String studentId,
    required String title,
    required String filename,
    required Uint8List bytes,
  }) async {
    final response = await api.upload(
      '/people/documents/',
      field: 'file',
      filename: filename,
      bytes: bytes,
      fields: {'student': studentId, 'title': title},
    );
    return (response.data as Map?)?.cast<String, dynamic>() ?? const {};
  }

  Future<void> deleteStudentDocument(String id) =>
      api.delete('/people/documents/$id/');

  // --- staff ---------------------------------------------------------------

  Future<List<Map<String, dynamic>>> staff({String? status, String? search}) =>
      _list(
        '/people/staff/',
        query: {'page_size': '200', 'status': ?status, 'search': ?search},
      );

  Future<Map<String, dynamic>> staffMember(String id) async {
    final response = await api.get('/people/staff/$id/', cache: false);
    return (response.data as Map).cast<String, dynamic>();
  }

  Future<Map<String, dynamic>> createStaff(Map<String, dynamic> fields) async {
    final response = await api.post('/people/staff/', body: fields);
    return (response.data as Map).cast<String, dynamic>();
  }

  Future<Map<String, dynamic>> updateStaff(
    String id,
    Map<String, dynamic> fields,
  ) async {
    final response = await api.patch('/people/staff/$id/', body: fields);
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Soft, like [deleteStudent]: the personnel file leaves the list and the
  /// signatures, uploads and audit rows that point at it survive.
  Future<void> deleteStaff(String id) => api.delete('/people/staff/$id/');

  /// Give a staff member a login on their own phone, with these roles. No
  /// credential is issued — they sign in with an OTP and set their own PIN.
  /// Re-posting is how roles change later: the account is matched, not remade.
  Future<Map<String, dynamic>> grantStaffAccount(
    String id, {
    List<String> roles = const [],
  }) async {
    final response = await api.post(
      '/people/staff/$id/account/',
      body: {'roles': roles},
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  // --- accounts and roles ---------------------------------------------------

  Future<List<Map<String, dynamic>>> users({
    bool? active,
    String? role,
    String? search,
  }) => _list(
    '/admin/users/',
    query: {
      'page_size': '200',
      'is_active': ?active?.toString(),
      'roles__code': ?role,
      'search': ?search,
    },
  );

  Future<Map<String, dynamic>> user(String id) async {
    final response = await api.get('/admin/users/$id/', cache: false);
    return (response.data as Map).cast<String, dynamic>();
  }

  Future<Map<String, dynamic>> createUser({
    required String phone,
    String firstName = '',
    String lastName = '',
    String email = '',
    List<String> roles = const [],
  }) async {
    final response = await api.post(
      '/admin/users/',
      body: {
        'phone': phone,
        'first_name': firstName,
        'last_name': lastName,
        'email': email,
        'roles': roles,
      },
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  /// The whole set, not a delta — the server audits the before and after.
  Future<Map<String, dynamic>> setUserRoles(
    String id,
    List<String> roles,
  ) async {
    final response = await api.put(
      '/admin/users/$id/roles/',
      body: {'roles': roles},
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Deactivating also revokes every live session server-side, so a suspended
  /// account cannot keep working from a phone that is already signed in.
  Future<Map<String, dynamic>> setUserActive(String id, bool active) async {
    final response = await api.patch(
      '/admin/users/$id/',
      body: {'is_active': active},
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  Future<List<Map<String, dynamic>>> roles() =>
      _list('/admin/roles/', query: {'page_size': '100'});

  // --- assessment ----------------------------------------------------------

  /// The whole entry screen for one subject and class: component columns plus
  /// one row per registered student, with the marks already stored.
  Future<Map<String, dynamic>> markSheet({
    required String term,
    required String subject,
    String? classArm,
  }) async {
    final response = await api.get(
      '/assessment/scores/sheet/',
      query: {'term': term, 'subject': subject, 'class_arm': ?classArm},
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  /// One component, a whole class, one request. Queued when offline: the
  /// server settles conflicts by `version`, so a replay is safe.
  ///
  /// Like [markAttendance], a 422 is data rather than an exception: the body
  /// names the rows it refused — a conflict, an unregistered pupil, a mark
  /// above the component's maximum — and nothing was written.
  Future<ApiResponse> submitScores({
    required String term,
    required String subject,
    required String component,
    String? classArm,
    required List<Map<String, dynamic>> rows,
  }) => api.post(
    '/assessment/scores/bulk/',
    body: {
      'term': term,
      'subject': subject,
      'component': component,
      'class_arm': ?classArm,
      'rows': rows,
    },
    queue: true,
    label: 'Marks for ${rows.length} student${rows.length == 1 ? '' : 's'}',
    allow: {422},
  );

  /// Already scoped server-side: a guardian gets their wards' results and a
  /// student their own, so this needs no student filter and cannot leak one.
  Future<List<Map<String, dynamic>>> termResults({String? term}) =>
      _list('/assessment/term-results/', query: {'term': ?term});

  Future<List<Map<String, dynamic>>> subjectResults({required String term}) =>
      _list(
        '/assessment/subject-results/',
        query: {'registration__term': term},
      );

  /// The whole cohort as a grid — students down, subjects across, already
  /// pivoted server-side. `cells` on each row is column-aligned with
  /// `subjects`, so a table renders without looking anything up.
  Future<Map<String, dynamic>> broadsheet({
    required String term,
    String? classArm,
  }) async {
    final response = await api.get(
      '/assessment/broadsheet/',
      query: {'term': term, 'class_arm': ?classArm},
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  /// The same broadsheet as the printed sheet an exams office pins up.
  Future<Uint8List> broadsheetPdf({required String term, String? classArm}) =>
      api.download(
        '/assessment/broadsheet/',
        query: {'term': term, 'class_arm': ?classArm, 'format': 'pdf'},
      );

  /// One student's report card, by term result id. Scoped server-side like
  /// [termResults]: a guardian may print their own child's and nobody else's.
  Future<Uint8List> reportCard(String termResultId) =>
      api.download('/assessment/term-results/$termResultId/report_card/');

  /// Every term the student has a result for, oldest first, with the CGPA and
  /// credit units the last one left behind. Readable by anyone the server lets
  /// see the student at all — the printed sheet is the part that is gated.
  Future<Map<String, dynamic>> transcript(String studentId) async {
    final response = await api.get('/assessment/transcript/$studentId/');
    return (response.data as Map).cast<String, dynamic>();
  }

  /// The signed, printable transcript. Needs `assessment.generate_report`:
  /// only the registrar's office sends one of these out of the building.
  Future<Uint8List> transcriptPdf(String studentId) => api.download(
    '/assessment/transcript/$studentId/',
    query: {'format': 'pdf'},
  );

  // --- attendance ----------------------------------------------------------

  /// The daily register only. `subject__isnull` is what keeps a per-period
  /// record out of it: the two coexist by design on the server, so without the
  /// filter a morning register would show a chemistry period's status — and,
  /// worse, send that row's `version` back, which is a conflict reported
  /// against a record the teacher never opened.
  Future<List<Map<String, dynamic>>> attendance({
    required String classArm,
    required String date,
  }) => _list(
    '/attendance/students/',
    query: {
      'class_arm': classArm,
      'date': date,
      'subject__isnull': 'true',
      'page_size': '200',
    },
  );

  /// A 422 comes back as *data*, like the roster import: the body is a per-row
  /// report (`{created, updated, errors: [...]}`), not the error envelope, and
  /// that report is the whole reason rows carry a version. Nothing was written
  /// when errors are present — the batch is one transaction.
  Future<ApiResponse> markAttendance({
    required String term,
    required String classArm,
    required String date,
    required List<Map<String, dynamic>> rows,
  }) => api.post(
    '/attendance/students/bulk/',
    body: {'term': term, 'class_arm': classArm, 'date': date, 'rows': rows},
    queue: true,
    label: 'Attendance for $date',
    allow: {422},
  );

  /// Per-student totals for a term — present, absent, and the percentage that
  /// goes on a report card. Server-side aggregation, so a class of forty over
  /// a term is one small response rather than five thousand rows.
  Future<List<Map<String, dynamic>>> attendanceSummary({
    required String term,
    String? classArm,
  }) => _list(
    '/attendance/students/summary/',
    query: {'term': term, 'class_arm': ?classArm},
  );

  // --- finance -------------------------------------------------------------

  Future<Map<String, dynamic>> statement(String studentId) async {
    final response = await api.get('/finance/statement/$studentId/');
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Bills, already scoped server-side: a parent sees their children's, a
  /// bursar the school's. `owing` is the counter's working list — issued or
  /// part-paid and still short — and `search` its lookup: invoice number,
  /// child's name, admission or matriculation number.
  Future<List<Map<String, dynamic>>> invoices({
    String? status,
    String? search,
    bool? owing,
  }) => _list(
    '/finance/invoices/',
    query: {
      'page_size': '100',
      'status': ?status,
      'search': ?search,
      'owing': ?owing?.toString(),
    },
  );

  /// The price list. Readable by anyone signed in; only the bursar bills from
  /// it. `is_active` is how last term's prices are retired without unpicking
  /// the invoices already raised from them.
  Future<List<Map<String, dynamic>>> feeStructures({bool? active}) => _list(
    '/finance/fee-structures/',
    query: {'page_size': '100', 'is_active': ?active?.toString()},
  );

  /// Writes to the price list itself — one price for a whole cohort, which is
  /// where a fee is meant to be changed. Every one of them runs `fee_changed`
  /// on the server: the new price reaches the *draft* invoices raised from the
  /// list, and stops at the ones already issued, because a bill a parent is
  /// holding must not move under them.
  ///
  /// Never queued. A replayed edit would re-apply an hour-old price over
  /// whatever was decided since.
  Future<Map<String, dynamic>> createFeeStructure(
    Map<String, dynamic> fields,
  ) => _write('POST', '/finance/fee-structures/', fields);

  Future<Map<String, dynamic>> updateFeeStructure(
    String id,
    Map<String, dynamic> fields,
  ) => _write('PATCH', '/finance/fee-structures/$id/', fields);

  /// Takes the structure's items with it, and is refused with 409
  /// `DEPENDENTS_EXIST` where anything else points at it. Retiring
  /// (`is_active: false`) is the usual move — it keeps last term's billing
  /// readable and takes the list out of the picker.
  Future<void> deleteFeeStructure(String id) =>
      api.delete('/finance/fee-structures/$id/');

  Future<Map<String, dynamic>> createFeeItem(Map<String, dynamic> fields) =>
      _write('POST', '/finance/fee-items/', fields);

  Future<Map<String, dynamic>> updateFeeItem(
    String id,
    Map<String, dynamic> fields,
  ) => _write('PATCH', '/finance/fee-items/$id/', fields);

  Future<void> deleteFeeItem(String id) =>
      api.delete('/finance/fee-items/$id/');

  /// What a line may be charged for, from the server's own `FeeCategory`.
  /// Read once per screen; [feeCategories] in `fees_screen.dart` is the
  /// fallback when the call fails, which is the offline case.
  Future<List<Map<String, dynamic>>> feeCategoryChoices() =>
      _list('/finance/fee-items/categories/');

  /// Raise one invoice per student in a structure's cohort. Safe to run
  /// again: a second run bills only the students who joined since, and never
  /// re-prices a bill somebody has part-paid.
  ///
  /// Returns the row report — `{created, updated, errors: [...]}` — for both
  /// outcomes, exactly like the roster import: a 422 means nothing was
  /// written and the errors are the thing to render, not throw.
  ///
  /// Never queued: an outbox replay is a second billing run.
  Future<Map<String, dynamic>> generateInvoices({
    required String structure,
    List<String>? students,
  }) async {
    final response = await api.post(
      '/finance/invoices/generate/',
      body: {'structure': structure, 'students': ?students},
    );
    return (response.data as Map?)?.cast<String, dynamic>() ?? const {};
  }

  /// Draft to issued — this is what makes a bill visible to a parent, and
  /// what starts the due date running.
  Future<Map<String, dynamic>> issueInvoice(String id) async {
    final response = await api.post('/finance/invoices/$id/issue/');
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Write a bill off. Kept, never deleted: a waiver is a decision, and the
  /// reason is what an auditor reads back.
  Future<Map<String, dynamic>> waiveInvoice(
    String id, {
    required String reason,
  }) async {
    final response = await api.post(
      '/finance/invoices/$id/waive/',
      body: {'reason': reason},
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Void a bill raised in error. The server refuses while a successful
  /// payment is attached — reverse those first.
  Future<Map<String, dynamic>> cancelInvoice(
    String id, {
    required String reason,
  }) async {
    final response = await api.post(
      '/finance/invoices/$id/cancel/',
      body: {'reason': reason},
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Rewrite one family's bill: its lines, and optionally its discount.
  ///
  /// `lines` is the whole bill after the edit, not the change — the server
  /// replaces the set, so two people adjusting the same invoice land on
  /// whichever list arrived last instead of compounding each other's edits.
  /// It refuses a cancelled or waived bill, and any total below what has
  /// already been paid.
  ///
  /// Never queued: an outbox replay would overwrite whatever was decided in
  /// between with a bill from an hour ago.
  Future<Map<String, dynamic>> adjustInvoice(
    String id, {
    required List<Map<String, dynamic>> lines,
    required String reason,
    String? discount,
  }) async {
    final response = await api.post(
      '/finance/invoices/$id/adjust/',
      body: {'lines': lines, 'reason': reason, 'discount': ?discount},
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Cash, transfer, POS or cheque taken at the counter. Never queued: a
  /// receipt replayed out of an outbox is money counted twice.
  Future<Map<String, dynamic>> recordPayment(
    String invoiceId, {
    required String amount,
    String method = 'CASH',
    String payerName = '',
    String payerPhone = '',
    String note = '',
  }) async {
    final response = await api.post(
      '/finance/invoices/$invoiceId/pay/',
      body: {
        'amount': amount,
        'method': method,
        'payer_name': payerName,
        'payer_phone': payerPhone,
        'note': note,
      },
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  /// The invoice as a PDF — what the office hands over or emails.
  Future<Uint8List> invoiceDocument(String id) =>
      api.download('/finance/invoices/$id/document/');

  /// The receipt for a settled payment. The server refuses one for anything
  /// that is not SUCCESS.
  Future<Uint8List> receipt(String paymentId) =>
      api.download('/finance/payments/$paymentId/receipt/');

  Future<List<String>> paymentGateways() async {
    final response = await api.get('/finance/invoices/gateways/');
    return ((response.data as Map)['gateways'] as List? ?? const [])
        .cast<String>();
  }

  /// Opens a checkout and returns the URL to send the payer to. Never queued:
  /// a payment replayed from an outbox is a second charge.
  Future<Map<String, dynamic>> checkout({
    required String invoiceId,
    required String gateway,
    num? amount,
  }) async {
    final response = await api.post(
      '/finance/invoices/$invoiceId/checkout/',
      body: {'gateway': gateway, if (amount != null) 'amount': '$amount'},
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  Future<Map<String, dynamic>> verifyPayment(String paymentId) async {
    final response = await api.post('/finance/payments/$paymentId/verify/');
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Receipts. Already scoped server-side, the same way results are: a parent
  /// sees their own, a bursar sees the school's.
  Future<List<Map<String, dynamic>>> payments({
    String? invoice,
    String? status,
  }) => _list(
    '/finance/payments/',
    query: {'invoice': ?invoice, 'status': ?status, 'page_size': '100'},
  );

  /// Bounced cheque, failed settlement, receipt keyed against the wrong bill.
  /// The invoice balance is recomputed and the reason is kept on the record —
  /// money is never deleted, only reversed with its reason attached.
  Future<Map<String, dynamic>> reversePayment(
    String id, {
    required String reason,
  }) async {
    final response = await api.post(
      '/finance/payments/$id/reverse/',
      body: {'reason': reason},
    );
    return (response.data as Map).cast<String, dynamic>();
  }

  /// Re-ask the gateway about every checkout its webhook never reported on,
  /// and abandon the ones old enough to be dead. Runs half-hourly on its own;
  /// this is the bursar's "do it now" for the morning's payments.
  Future<Map<String, dynamic>> sweepPayments() async {
    final response = await api.post('/finance/reports/sweep/');
    return (response.data as Map).cast<String, dynamic>();
  }

  // --- communication -------------------------------------------------------

  Future<List<Map<String, dynamic>>> announcements() =>
      _list('/communication/announcements/');

  Future<int> unreadCount() async {
    final response = await api.get(
      '/communication/inbox/unread_count/',
      cache: false,
    );
    return (response.data as Map)['unread'] as int? ?? 0;
  }

  Future<void> markInboxRead() => api.post('/communication/inbox/read/');

  Future<ApiResponse> createAnnouncement({
    required String title,
    required String body,
    List<String> audienceRoles = const [],
    List<String> channels = const [],
  }) => api.post(
    '/communication/announcements/',
    body: {
      'title': title,
      'body': body,
      'audience_roles': audienceRoles,
      'channels': channels,
    },
  );

  Future<void> publishAnnouncement(String id) =>
      api.post('/communication/announcements/$id/publish/');

  /// Everything the school sent, with what the provider said about it. The
  /// answer to "the parents say they never got the text", which until now had
  /// no screen at all — `delivery_status` and `error` were written and never
  /// read. Needs `communication.send_sms`.
  Future<List<Map<String, dynamic>>> messageLog({
    String? channel,
    String? status,
    String? announcement,
  }) => _list(
    '/communication/messages/',
    query: {
      'page_size': '100',
      'channel': ?channel,
      'status': ?status,
      'announcement': ?announcement,
    },
  );

  // --- reports -------------------------------------------------------------

  Future<Map<String, dynamic>> overview() async {
    final response = await api.get('/reports/overview/');
    return (response.data as Map).cast<String, dynamic>();
  }

  Future<List<Map<String, dynamic>>> defaulters() async {
    final response = await api.get('/finance/reports/defaulters/');
    return ((response.data as Map)['defaulters'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
  }
}

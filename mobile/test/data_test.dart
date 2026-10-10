import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';
import 'package:vyterlix_mobile/files.dart';
import 'package:vyterlix_mobile/models_data.dart';
import 'package:vyterlix_mobile/screens/upload_screen.dart' show describeCounts;

import 'support.dart';

class FakeFiles implements FileChooser {
  PickedFile? next = PickedFile(
    name: 'sales.csv',
    bytes: utf8.encode('date,total\n2026-10-01,10\n'),
  );
  int asked = 0;

  @override
  Future<PickedFile?> chooseSpreadsheet() async {
    asked++;
    return next;
  }
}

Map<String, dynamic> importJson(
  String id, {
  String status = 'uploaded',
  String dataset = 'sales',
  String filename = 'sales.csv',
  int rows = 120,
  int valid = 0,
  int invalid = 0,
  int duplicate = 0,
  int imported = 0,
  String? sheet,
}) => {
  'id': id,
  'dataset': dataset,
  'source': 'csv',
  'status': status,
  'original_filename': filename,
  'file_size_bytes': 2048,
  'sheet_name': sheet,
  'header_row': 1,
  'row_count': rows,
  'valid_count': valid,
  'invalid_count': invalid,
  'duplicate_count': duplicate,
  'imported_count': imported,
  'uploaded_by_user_id': 'u1',
  'uploaded_by_name': 'Jo Baker',
  'created_at': '2026-10-09T13:05:00',
  'imported_at': status == 'imported' ? '2026-10-09T13:10:00' : null,
  'undone_at': status == 'undone' ? '2026-10-09T14:00:00' : null,
  'file_deleted_at': null,
};

Map<String, dynamic> previewJson({
  bool needsSheet = false,
  List<String> sheets = const [],
}) => {
  'headers': ['Date', 'Total', 'Ref'],
  'sample_rows': [
    ['01/10/2026', '10.00', 'A1'],
    ['02/10/2026', '12.50', 'A2'],
  ],
  'row_count': 120,
  'sheets': sheets,
  'needs_sheet': needsSheet,
  'encoding': 'utf-8',
  'delimiter': ',',
};

Map<String, dynamic> uploadedJson(
  String id, {
  Map<String, dynamic>? duplicate,
  bool needsSheet = false,
  String status = 'uploaded',
  String? sheet,
}) => {
  ...importJson(id, status: status, sheet: sheet),
  'preview': previewJson(
    needsSheet: needsSheet,
    sheets: needsSheet ? ['Takings', 'Summary'] : const [],
  ),
  'duplicate_of': duplicate,
};

Map<String, dynamic> mappingJson({
  bool needsVat = false,
  bool ready = false,
  Map<String, String> saved = const {},
  String? from = 'automatic',
}) => {
  'dataset': 'sales',
  'status': 'uploaded',
  'fields': [
    {
      'key': 'sold_on',
      'label': 'Date of sale',
      'help': 'The day it was sold.',
      'type': 'date',
      'required': true,
      'one_of': null,
    },
    {
      'key': 'total',
      'label': 'Total',
      'help': 'What was taken.',
      'type': 'money',
      'required': true,
      'one_of': null,
    },
    {
      'key': 'reference',
      'label': 'Reference',
      'help': 'An invoice number.',
      'type': 'text',
      'required': false,
      'one_of': null,
    },
  ],
  'headers': ['Date', 'Total', 'Ref'],
  'mapping': saved,
  'options': <String, dynamic>{},
  'suggested_mapping': {'sold_on': 'Date', 'total': 'Total'},
  'suggested_options': {'vat_inclusive': null, 'default_vat_rate': null},
  'suggestion_from': from,
  'saved_source': null,
  'needs_vat_options': needsVat,
  'vat_rates': ['20', '5', '0'],
  'issues': <dynamic>[],
  'ready': ready,
};

Map<String, dynamic> validationJson({
  bool canImport = true,
  int valid = 100,
  int invalid = 15,
  List<String> warnings = const [],
}) => {
  'validated_at': '2026-10-09T13:06:00',
  'rows': 120,
  'valid': valid,
  'invalid': invalid,
  'duplicate': 5,
  'problems': invalid == 0
      ? []
      : [
          {
            'code': 'bad_date',
            'field': 'sold_on',
            'count': invalid,
            'rows': [4, 9, 12],
            'example': 'Row 4: “31/02/2026” is not a real date.',
          },
        ],
  'totals': {'net': '1000.00', 'vat': '200.00', 'gross': '1200.00'},
  'date_from': '2026-10-01',
  'date_to': '2026-10-31',
  'warnings': [
    for (final w in warnings) {'code': 'w', 'message': w},
  ],
  'can_import': canImport,
};

Map<String, dynamic> resultJson() => {
  'data_import': importJson('i1', status: 'imported', imported: 100),
  'created': {'sales': 100, 'sale_lines': 100, 'customers': 0},
  'skipped_duplicates': 3,
  'skipped_invalid': 1,
};

FakeServer dataServer({Map<String, dynamic>? upload}) {
  final server = happyServer()
    ..json('GET /organizations/b1/data-quality', {
      'as_of': '2026-10-09',
      'score': 97,
      'band': 'good',
      'headline': 'Your data quality is good: 97 out of 100.',
      'datasets': [
        {
          'dataset': 'sales',
          'label': 'Sales',
          'records': 15865,
          'score': 95,
          'band': 'good',
          'weight': 50,
          'components': [],
        },
        {
          'dataset': 'stock_movements',
          'label': 'Stock movements',
          'records': 0,
          'score': null,
          'band': null,
          'weight': 5,
          'components': [],
        },
      ],
      'missing_datasets': [],
      'months': [],
      'imports': [],
      'origins': [],
      'issues': [
        {
          'dataset': 'sales',
          'issue_type': 'costs',
          'severity': 'warning',
          'period_start': null,
          'period_end': null,
          'affected_count': 10,
          'message': '0% of your sales value has no cost of goods.',
          'fix': 'Add the cost to each sale line.',
          'details': {},
        },
        {
          'dataset': 'expenses',
          'issue_type': 'cat',
          'severity': 'critical',
          'period_start': null,
          'period_end': null,
          'affected_count': 1,
          'message': '1 expense has no cost category.',
          'fix': 'Give each expense a category.',
          'details': {},
        },
      ],
    })
    ..json('GET /organizations/b2/data-quality', {
      'as_of': '2026-10-09',
      'score': null,
      'band': null,
      'headline': 'Add some sales to get a score.',
      'datasets': [],
      'missing_datasets': [],
      'months': [],
      'imports': [],
      'origins': [],
      'issues': [],
    })
    ..json('POST /organizations/b1/imports', upload ?? uploadedJson('i1'))
    ..json('GET /organizations/b1/imports/i1', uploadedJson('i1'))
    ..json('GET /organizations/b1/imports/i1/mapping', mappingJson())
    ..json(
      'PUT /organizations/b1/imports/i1/mapping',
      mappingJson(ready: true, saved: {'sold_on': 'Date', 'total': 'Total'}),
    )
    ..json('POST /organizations/b1/imports/i1/validate', validationJson())
    ..json('GET /organizations/b1/imports/i1/validation', validationJson())
    ..json('POST /organizations/b1/imports/i1/import', resultJson())
    ..json('GET /organizations/b1/imports/i1/records', {
      'import_id': 'i1',
      'counts': {'sales': 100, 'sale_lines': 100},
    })
    ..json('POST /organizations/b1/imports/i1/undo', {
      'data_import': importJson('i1', status: 'undone'),
      'removed': {'sales': 100, 'sale_lines': 100},
    })
    ..json('GET /organizations/b1/imports', [
      importJson('i1', status: 'uploaded'),
      importJson(
        'i2',
        status: 'mapped',
        filename: 'costs.xlsx',
        dataset: 'expenses',
      ),
      importJson(
        'i3',
        status: 'validated',
        filename: 'stock.csv',
        dataset: 'stock_movements',
      ),
      importJson('i4', status: 'imported', filename: 'march.csv', imported: 90),
      importJson('i5', status: 'undone', filename: 'old.csv'),
      importJson('i6', status: 'failed', filename: 'broken.csv'),
    ]);
  return server;
}

Future<Harness> openData(
  WidgetTester tester,
  FakeServer server, {
  FakeFiles? files,
  String name = 'Fakeham Bakery',
}) async {
  tester.view.physicalSize = const Size(800, 3200);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  final h = await pumpApp(
    tester,
    server,
    store: MemorySessionStore()..refreshToken = 'saved',
    files: files ?? FakeFiles(),
  );
  await tester.tap(find.text(name));
  await tester.pumpAndSettle();
  await tester.tap(
    find.descendant(
      of: find.byType(NavigationBar),
      matching: find.text('More'),
    ),
  );
  await tester.pumpAndSettle();
  await tester.tap(find.text('Your data'));
  await tester.pumpAndSettle();
  return h;
}

Future<void> startUpload(WidgetTester tester, {String? kind}) async {
  await tester.tap(find.text('Upload a file'));
  await tester.pumpAndSettle();
  if (kind != null) {
    await tester.tap(find.widgetWithText(ChoiceChip, kind));
    await tester.pumpAndSettle();
  }
  await tester.tap(find.text('Choose a file and read it'));
  await tester.pumpAndSettle();
}

Future<void> toMapping(WidgetTester tester) async {
  await startUpload(tester);
  await tester.tap(find.text('Match the columns'));
  await tester.pumpAndSettle();
}

Future<void> toResults(WidgetTester tester) async {
  await toMapping(tester);
  await tester.tap(find.text('Save and continue'));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Check the rows'));
  await tester.pumpAndSettle();
}

void main() {
  group('what comes from the server', () {
    test('an upload is read with its preview and any earlier copy', () {
      final u = UploadedImport.fromJson(
        uploadedJson(
          'x',
          duplicate: {
            'id': 'o',
            'original_filename': 'old.csv',
            'status': 'imported',
            'created_at': '2026-09-01T10:00:00',
          },
          needsSheet: true,
        ),
      );
      expect(
        (u.record.id, u.record.dataset, u.record.rowCount),
        ('x', 'sales', 120),
      );
      expect(u.preview!.headers, ['Date', 'Total', 'Ref']);
      expect(u.preview!.sampleRows.first, ['01/10/2026', '10.00', 'A1']);
      expect(u.preview!.needsSheet, true);
      expect(u.preview!.sheets, ['Takings', 'Summary']);
      expect(u.duplicateOf!.filename, 'old.csv');
      expect(
        UploadedImport.fromJson({
          ...importJson('y'),
          'preview': null,
          'duplicate_of': null,
        }).preview,
        isNull,
      );
    });

    test('a mapping, a check and a result are read', () {
      final m = Mapping.fromJson(mappingJson(needsVat: true));
      expect(m.fields.map((f) => f.required), [true, true, false]);
      expect(m.suggested, {'sold_on': 'Date', 'total': 'Total'});
      expect(m.needsVatOptions, true);
      expect(m.vatRates, ['20', '5', '0']);
      expect(m.ready, false);
      final v = Validation.fromJson(validationJson(warnings: ['Careful']));
      expect(
        (v.rows, v.valid, v.invalid, v.duplicate, v.canImport),
        (120, 100, 15, 5, true),
      );
      expect(
        (v.net, v.vat, v.gross, v.dateFrom),
        ('1000.00', '200.00', '1200.00', '2026-10-01'),
      );
      expect(v.problems.single.rows, [4, 9, 12]);
      expect(v.warnings, ['Careful']);
      final r = ImportResult.fromJson(resultJson());
      expect(
        (
          r.created['sales'],
          r.skippedDuplicates,
          r.skippedInvalid,
          r.record.status,
        ),
        (100, 3, 1, 'imported'),
      );
    });

    test('a job and the data quality are read', () {
      final j = JobState.fromJson({
        'status': 'running',
        'percent': 40,
        'error_message': null,
      });
      expect((j.finished, j.percent), (false, 40));
      expect(
        JobState.fromJson({
          'status': 'failed',
          'percent': null,
          'error_message': 'Bad',
        }).finished,
        isTrue,
      );
      final q = DataQuality.fromJson({
        'score': 97,
        'band': 'good',
        'headline': 'h',
        'datasets': [
          {'label': 'Sales', 'records': 5, 'score': 95, 'band': 'good'},
        ],
        'issues': [
          {'severity': 'warning', 'message': 'm', 'fix': 'f'},
        ],
      });
      expect(
        (q.score, q.datasets.single.records, q.issues.single.fix),
        (97, 5, 'f'),
      );
    });

    test('counts of what was added are put in words', () {
      expect(
        describeCounts({'sales': 1200, 'sale_lines': 3, 'customers': 0}),
        '1,200 sales, 3 sale lines',
      );
      expect(describeCounts({}), 'nothing');
      expect(
        describeCounts({'stock_movements': 2, 'odd_table': 4}),
        '2 stock movements, 4 odd_table',
      );
      expect(datasetLabel('stock_movements'), 'Stock movements');
      expect(datasetLabel('what'), 'what');
    });
  });

  group('Your data', () {
    testWidgets(
      'says how good the data is, what to fix first and how each kind is doing',
      (tester) async {
        await openData(tester, dataServer());
        expect(find.byKey(const ValueKey('data-score')), findsOneWidget);
        expect(find.text('97'), findsOneWidget);
        expect(
          find.text('Your data quality is good: 97 out of 100.'),
          findsOneWidget,
        );
        expect(find.text('What to fix first'), findsOneWidget);
        expect(find.text('Fix first'), findsOneWidget);
        expect(find.text('Should fix'), findsOneWidget);
        expect(find.text('Give each expense a category.'), findsOneWidget);
        expect(find.text('15865 records'), findsOneWidget);
        expect(find.text('Stock movements'), findsOneWidget);
      },
    );

    testWidgets('an owner can upload and see the history', (tester) async {
      await openData(tester, dataServer());
      expect(find.text('Upload a file'), findsOneWidget);
      expect(find.text('Import history'), findsOneWidget);
    });

    testWidgets('a viewer is only told only the owner can add or change data', (
      tester,
    ) async {
      await openData(tester, dataServer(), name: 'Second Shop');
      expect(find.text('Add some sales to get a score.'), findsOneWidget);
      expect(
        find.text('Only the owner can add or change data.'),
        findsOneWidget,
      );
      expect(find.text('Upload a file'), findsNothing);
    });

    testWidgets('a problem loading can be tried again', (tester) async {
      final server = dataServer()
        ..on(
          'GET /organizations/b1/data-quality',
          (_) => errorResponse(
            500,
            'internal_error',
            'An unexpected error occurred',
          ),
        );
      await openData(tester, server);
      expect(find.text('An unexpected error occurred'), findsOneWidget);
      server.json('GET /organizations/b1/data-quality', {
        'as_of': '2026-10-09',
        'score': 80,
        'band': 'fair',
        'headline': 'Fair.',
        'datasets': [],
        'issues': [],
      });
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('80'), findsOneWidget);
    });
  });

  group('choosing and reading a file', () {
    testWidgets('starts by asking what is in it, with Sales picked', (
      tester,
    ) async {
      await openData(tester, dataServer());
      await tester.tap(find.text('Upload a file'));
      await tester.pumpAndSettle();
      expect(find.text('What is in the file?'), findsOneWidget);
      for (final label in [
        'Sales',
        'Expenses',
        'Customers',
        'Suppliers',
        'Products',
        'Stock movements',
      ]) {
        expect(find.widgetWithText(ChoiceChip, label), findsOneWidget);
      }
      expect(
        tester
            .widget<ChoiceChip>(find.widgetWithText(ChoiceChip, 'Sales'))
            .selected,
        isTrue,
      );
      expect(find.text('1. Your file'), findsOneWidget);
    });

    testWidgets('sends the file and what it holds, then shows what was read', (
      tester,
    ) async {
      final server = dataServer();
      final files = FakeFiles()
        ..next = PickedFile(
          name: 'costs.xlsx',
          bytes: utf8.encode('PK-pretend-spreadsheet'),
        );
      await openData(tester, server, files: files);
      await startUpload(tester, kind: 'Expenses');
      final sent = server.to('POST /organizations/b1/imports').single;
      expect(sent.headers['content-type'], startsWith('multipart/form-data'));
      expect(sent.headers['Authorization'], 'Bearer access-2');
      expect(sent.body, contains('name="dataset"'));
      expect(sent.body, contains('expenses'));
      expect(sent.body, contains('filename="costs.xlsx"'));
      expect(sent.body, contains('PK-pretend-spreadsheet'));
      expect(find.text('sales.csv'), findsOneWidget);
      expect(find.text('Sales · 2 KB'), findsOneWidget);
      expect(
        find.text(
          'We read 120 rows. Here are the first few. Check that the column headings look right.',
        ),
        findsOneWidget,
      );
      expect(find.text('Date'), findsOneWidget);
      expect(find.text('12.50'), findsOneWidget);
    });

    testWidgets('backing out of the chooser does nothing', (tester) async {
      final server = dataServer();
      final files = FakeFiles()..next = null;
      await openData(tester, server, files: files);
      await startUpload(tester);
      expect(files.asked, 1);
      expect(server.to('POST /organizations/b1/imports'), isEmpty);
      expect(find.text('What is in the file?'), findsOneWidget);
    });

    testWidgets('an empty file is refused before it is sent', (tester) async {
      final server = dataServer();
      final files = FakeFiles()
        ..next = PickedFile(name: 'empty.csv', bytes: const []);
      await openData(tester, server, files: files);
      await startUpload(tester);
      expect(find.text('Choose a file first.'), findsOneWidget);
      expect(server.to('POST /organizations/b1/imports'), isEmpty);
    });

    testWidgets(
      'a refusal from the server is shown and the person can try another file',
      (tester) async {
        final server = dataServer()
          ..on(
            'POST /organizations/b1/imports',
            (_) => errorResponse(
              422,
              'unreadable_file',
              'We could not read that file.',
            ),
          );
        await openData(tester, server);
        await startUpload(tester);
        expect(find.text('We could not read that file.'), findsOneWidget);
        expect(find.text('Choose a file and read it'), findsOneWidget);
      },
    );

    testWidgets('a file like one uploaded before is pointed out', (
      tester,
    ) async {
      final server = dataServer(
        upload: uploadedJson(
          'i1',
          duplicate: {
            'id': 'o',
            'original_filename': 'sales-old.csv',
            'status': 'imported',
            'created_at': '2026-09-01T10:00:00',
          },
        ),
      );
      await openData(tester, server);
      await startUpload(tester);
      expect(
        find.textContaining(
          'This looks like a file you uploaded before: “sales-old.csv” on 01/09/2026 (imported).',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a workbook with several sheets asks which one', (
      tester,
    ) async {
      final server = dataServer(upload: uploadedJson('i1', needsSheet: true))
        ..json(
          'PATCH /organizations/b1/imports/i1',
          uploadedJson('i1', sheet: 'Takings'),
        );
      await openData(tester, server);
      await startUpload(tester);
      expect(find.text('Which sheet?'), findsOneWidget);
      expect(find.text('Match the columns'), findsNothing);
      await tester.tap(find.widgetWithText(ChoiceChip, 'Takings'));
      await tester.pumpAndSettle();
      expect(
        server.bodyOf(server.to('PATCH /organizations/b1/imports/i1').single),
        {'sheet_name': 'Takings'},
      );
      expect(find.text('Match the columns'), findsOneWidget);
    });
  });

  group('matching the columns', () {
    testWidgets('starts from our guess and says which are needed', (
      tester,
    ) async {
      await openData(tester, dataServer());
      await toMapping(tester);
      expect(find.text('Match your columns'), findsOneWidget);
      expect(
        find.text(
          'We have made a first guess at which column is which. Check them, and change any that are wrong.',
        ),
        findsOneWidget,
      );
      expect(find.text('Date of sale *'), findsOneWidget);
      expect(find.text('Total *'), findsOneWidget);
      expect(find.text('Reference'), findsOneWidget);
      expect(find.text('The day it was sold.'), findsOneWidget);
      expect(find.text('Date'), findsWidgets); // chosen for the date of sale
    });

    testWidgets('a mapping that was saved earlier is said to be that', (
      tester,
    ) async {
      final server = dataServer()
        ..json(
          'GET /organizations/b1/imports/i1/mapping',
          mappingJson(from: 'saved'),
        );
      await openData(tester, server);
      await toMapping(tester);
      expect(
        find.text(
          'We matched these from a mapping you saved. Check they are right.',
        ),
        findsOneWidget,
      );
    });

    testWidgets(
      'saving sends what was chosen, leaving out what is not in the file, and moves on',
      (tester) async {
        final server = dataServer();
        await openData(tester, server);
        await toMapping(tester);
        await tester.tap(find.byKey(const ValueKey('map-reference')));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Ref').last);
        await tester.pumpAndSettle();
        await tester.enterText(
          find.widgetWithText(TextField, 'Remember this as... (optional)'),
          ' Till export ',
        );
        await tester.tap(find.text('Save and continue'));
        await tester.pumpAndSettle();
        expect(
          server.bodyOf(
            server.to('PUT /organizations/b1/imports/i1/mapping').single,
          ),
          {
            'mapping': {
              'sold_on': 'Date',
              'total': 'Total',
              'reference': 'Ref',
            },
            'options': {},
            'save_as': 'Till export',
          },
        );
        expect(find.text('Columns matched'), findsOneWidget);
        expect(find.text('1. Your file'), findsOneWidget);
      },
    );

    testWidgets('a column can be marked as not in the file', (tester) async {
      final server = dataServer();
      await openData(tester, server);
      await toMapping(tester);
      await tester.tap(find.byKey(const ValueKey('map-total')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('(not in my file)').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Save and continue'));
      await tester.pumpAndSettle();
      expect(
        (server.bodyOf(
                  server.to('PUT /organizations/b1/imports/i1/mapping').single,
                )['mapping']
                as Map)
            .containsKey('total'),
        isFalse,
      );
    });

    testWidgets(
      'a file whose amounts may or may not include VAT asks, and sends the answer',
      (tester) async {
        final server = dataServer()
          ..json(
            'GET /organizations/b1/imports/i1/mapping',
            mappingJson(needsVat: true),
          );
        await openData(tester, server);
        await toMapping(tester);
        expect(find.text('About the amounts in this file'), findsOneWidget);
        await tester.tap(find.text('No, amounts are before VAT'));
        await tester.pumpAndSettle();
        await tester.tap(find.byKey(const ValueKey('vat-rate')));
        await tester.pumpAndSettle();
        await tester.tap(find.text('20%').last);
        await tester.pumpAndSettle();
        await tester.tap(find.text('Save and continue'));
        await tester.pumpAndSettle();
        expect(
          server.bodyOf(
            server.to('PUT /organizations/b1/imports/i1/mapping').single,
          )['options'],
          {'vat_inclusive': false, 'default_vat_rate': '20'},
        );
      },
    );

    testWidgets(
      'an incomplete mapping is refused with the reason, and stays on the page',
      (tester) async {
        final server = dataServer()
          ..on(
            'PUT /organizations/b1/imports/i1/mapping',
            (_) => errorResponse(
              422,
              'mapping_incomplete',
              'Choose a column for the date of sale.',
            ),
          );
        await openData(tester, server);
        await toMapping(tester);
        await tester.tap(find.text('Save and continue'));
        await tester.pumpAndSettle();
        expect(
          find.text('Choose a column for the date of sale.'),
          findsOneWidget,
        );
        expect(find.text('Match your columns'), findsOneWidget);
      },
    );

    testWidgets('going back returns to the file', (tester) async {
      await openData(tester, dataServer());
      await toMapping(tester);
      await tester.tap(find.text('Back'));
      await tester.pumpAndSettle();
      expect(find.text('Match the columns'), findsOneWidget);
    });
  });

  group('checking and importing', () {
    testWidgets(
      'shows what the check found, with the amounts and what needs fixing',
      (tester) async {
        await openData(tester, dataServer());
        await toResults(tester);
        expect(find.text('Check results'), findsOneWidget);
        expect(find.text('120'), findsOneWidget);
        expect(find.text('rows checked'), findsOneWidget);
        expect(find.text('ready to import'), findsOneWidget);
        expect(find.text('have a problem'), findsOneWidget);
        expect(find.text('repeats (skipped)'), findsOneWidget);
        expect(
          find.text(
            'The 100 rows ready to import add up to £1,000.00 before VAT (£200.00 VAT, £1,200.00 in total), dated 01/10/2026 to 31/10/2026.',
          ),
          findsOneWidget,
        );
        expect(find.text('What needs fixing'), findsOneWidget);
        expect(
          find.text('Row 4: “31/02/2026” is not a real date.'),
          findsOneWidget,
        );
        expect(find.text('15 rows, first: 4, 9, 12'), findsOneWidget);
        expect(find.text('Import 100 rows'), findsOneWidget);
      },
    );

    testWidgets(
      'warnings are shown and nothing can be imported when the server says so',
      (tester) async {
        final server = dataServer()
          ..json(
            'POST /organizations/b1/imports/i1/validate',
            validationJson(
              canImport: false,
              valid: 0,
              warnings: ['Most rows are repeats.'],
            ),
          );
        await openData(tester, server);
        await toResults(tester);
        expect(find.text('Most rows are repeats.'), findsOneWidget);
        expect(
          tester
              .widget<FilledButton>(
                find.widgetWithText(FilledButton, 'Import 0 rows'),
              )
              .onPressed,
          isNull,
        );
      },
    );

    testWidgets(
      'importing adds the rows, says what was added and what was skipped',
      (tester) async {
        final server = dataServer();
        await openData(tester, server);
        await toResults(tester);
        await tester.tap(find.text('Import 100 rows'));
        await tester.pumpAndSettle();
        expect(
          server.to('POST /organizations/b1/imports/i1/import'),
          hasLength(1),
        );
        expect(find.text('Imported'), findsOneWidget);
        expect(
          find.text('100 rows from “sales.csv” are now in your data.'),
          findsOneWidget,
        );
        expect(find.text('Added: 100 sales, 100 sale lines.'), findsOneWidget);
        expect(
          find.text(
            'Skipped: 3 already in your data, 1 that couldn\'t be read.',
          ),
          findsOneWidget,
        );
        expect(find.text('Undo this import'), findsOneWidget);
      },
    );

    testWidgets('a mistake can be undone, after being asked', (tester) async {
      final server = dataServer();
      await openData(tester, server);
      await toResults(tester);
      await tester.tap(find.text('Import 100 rows'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Undo this import'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Keep it'));
      await tester.pumpAndSettle();
      expect(server.to('POST /organizations/b1/imports/i1/undo'), isEmpty);
      await tester.tap(find.text('Undo this import'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Undo it'));
      await tester.pumpAndSettle();
      expect(server.to('POST /organizations/b1/imports/i1/undo'), hasLength(1));
      expect(find.text('This import was undone'), findsOneWidget);
      expect(find.text('Removed: 100 sales, 100 sale lines.'), findsOneWidget);
    });

    testWidgets('a refusal when checking or importing is shown', (
      tester,
    ) async {
      final server = dataServer()
        ..on(
          'POST /organizations/b1/imports/i1/validate',
          (_) => errorResponse(409, 'not_mapped', 'Match the columns first.'),
        );
      await openData(tester, server);
      await toMapping(tester);
      await tester.tap(find.text('Save and continue'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Check the rows'));
      await tester.pumpAndSettle();
      expect(find.text('Match the columns first.'), findsOneWidget);
      expect(find.text('Check the rows'), findsOneWidget);
    });

    testWidgets('the columns can be changed from the check step', (
      tester,
    ) async {
      await openData(tester, dataServer());
      await toMapping(tester);
      await tester.tap(find.text('Save and continue'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Change the columns'));
      await tester.pumpAndSettle();
      expect(find.text('Match your columns'), findsOneWidget);
    });
  });

  group('a big file, done in the background', () {
    FakeServer bigFile(List<Map<String, dynamic>> jobStates) {
      var polled = 0;
      return dataServer()
        ..on(
          'POST /organizations/b1/imports/i1/validate',
          (_) => errorResponse(
            409,
            'use_background_job',
            'This file has more than 5,000 rows, so it is processed in the background.',
          ),
        )
        ..on(
          'POST /organizations/b1/imports/i1/import',
          (_) => errorResponse(
            409,
            'use_background_job',
            'This file has more than 5,000 rows, so it is processed in the background.',
          ),
        )
        ..json('POST /organizations/b1/imports/i1/jobs', {
          'id': 'j1',
          'status': 'queued',
          'percent': null,
        })
        ..on(
          'GET /organizations/b1/jobs/j1',
          (_) => jsonResponse(
            jobStates[(polled++).clamp(0, jobStates.length - 1)],
          ),
        );
    }

    testWidgets('is checked by a job that is watched until it finishes', (
      tester,
    ) async {
      final server = bigFile([
        {'id': 'j1', 'status': 'running', 'percent': 40, 'error_message': null},
        {
          'id': 'j1',
          'status': 'succeeded',
          'percent': 100,
          'error_message': null,
        },
      ]);
      await openData(tester, server);
      await toResults(tester);
      expect(
        server.bodyOf(
          server.to('POST /organizations/b1/imports/i1/jobs').single,
        ),
        {'action': 'validate'},
      );
      for (var i = 0; i < 3; i++) {
        await tester.pump(const Duration(seconds: 2));
      }
      await tester.pumpAndSettle();
      expect(server.to('GET /organizations/b1/jobs/j1').length, 2);
      expect(find.text('Check results'), findsOneWidget);
      expect(find.text('Import 100 rows'), findsOneWidget);
    });

    testWidgets('is imported by a job, then what was added is read', (
      tester,
    ) async {
      final server = bigFile([
        {
          'id': 'j1',
          'status': 'succeeded',
          'percent': 100,
          'error_message': null,
        },
      ]);
      await openData(tester, server);
      await toResults(tester);
      await tester.tap(find.text('Import 100 rows'));
      await tester.pumpAndSettle();
      expect(
        server
            .to('POST /organizations/b1/imports/i1/jobs')
            .map(server.bodyOf)
            .map((b) => b['action']),
        ['validate', 'import'],
      );
      expect(find.text('Imported'), findsOneWidget);
      expect(find.text('Added: 100 sales, 100 sale lines.'), findsOneWidget);
    });

    testWidgets('a job that fails says why', (tester) async {
      final server = bigFile([
        {
          'id': 'j1',
          'status': 'failed',
          'percent': null,
          'error_message': 'The file could not be read any more.',
        },
      ]);
      await openData(tester, server);
      await toResults(tester);
      expect(find.text('The file could not be read any more.'), findsOneWidget);
      expect(find.text('Check the rows'), findsOneWidget);
    });
  });

  group('the import history', () {
    Future<void> openHistory(WidgetTester tester, FakeServer server) async {
      await openData(tester, server);
      await tester.tap(find.text('Import history'));
      await tester.pumpAndSettle();
    }

    testWidgets('lists every upload with its state, kind and size', (
      tester,
    ) async {
      await openHistory(tester, dataServer());
      expect(find.text('sales.csv'), findsOneWidget);
      expect(find.text('costs.xlsx'), findsOneWidget);
      for (final state in [
        'Uploaded',
        'Columns matched',
        'Checked',
        'Imported',
        'Undone',
        'Failed',
      ]) {
        expect(find.text(state), findsOneWidget);
      }
      expect(find.text('Expenses'), findsOneWidget);
      expect(
        find.text('120 rows · 09/10/2026, 13:05 · Jo Baker'),
        findsWidgets,
      );
      expect(find.text('90 rows added to your data.'), findsOneWidget);
    });

    testWidgets('an empty history says so', (tester) async {
      await openHistory(
        tester,
        dataServer()..json('GET /organizations/b1/imports', []),
      );
      expect(find.text('Nothing has been uploaded yet.'), findsOneWidget);
    });

    testWidgets('a problem loading can be tried again', (tester) async {
      final server = dataServer()
        ..on(
          'GET /organizations/b1/imports',
          (_) => errorResponse(
            500,
            'internal_error',
            'An unexpected error occurred',
          ),
        );
      await openHistory(tester, server);
      expect(find.text('An unexpected error occurred'), findsOneWidget);
      server.json('GET /organizations/b1/imports', []);
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Nothing has been uploaded yet.'), findsOneWidget);
    });

    testWidgets('one just uploaded carries on at the file', (tester) async {
      await openHistory(tester, dataServer());
      await tester.tap(find.text('sales.csv'));
      await tester.pumpAndSettle();
      expect(find.text('Match the columns'), findsOneWidget);
    });

    testWidgets('one whose columns are matched carries on at the check', (
      tester,
    ) async {
      final server = dataServer()
        ..json(
          'GET /organizations/b1/imports/i2',
          uploadedJson('i2', status: 'mapped'),
        )
        ..json(
          'GET /organizations/b1/imports/i2/mapping',
          mappingJson(
            ready: true,
            saved: {'sold_on': 'Date', 'total': 'Total'},
          ),
        );
      await openHistory(tester, server);
      await tester.tap(find.text('costs.xlsx'));
      await tester.pumpAndSettle();
      expect(find.text('Columns matched'), findsOneWidget);
      expect(find.text('Check the rows'), findsOneWidget);
    });

    testWidgets('one already checked carries on at the results', (
      tester,
    ) async {
      final server = dataServer()
        ..json(
          'GET /organizations/b1/imports/i3',
          uploadedJson('i3', status: 'validated'),
        )
        ..json(
          'GET /organizations/b1/imports/i3/mapping',
          mappingJson(ready: true),
        )
        ..json('GET /organizations/b1/imports/i3/validation', validationJson());
      await openHistory(tester, server);
      await tester.tap(find.text('stock.csv'));
      await tester.pumpAndSettle();
      expect(find.text('Check results'), findsOneWidget);
      expect(find.text('Import 100 rows'), findsOneWidget);
    });

    testWidgets('one that was imported can be undone from here', (
      tester,
    ) async {
      final server = dataServer()
        ..json(
          'GET /organizations/b1/imports/i4',
          uploadedJson('i4', status: 'imported'),
        )
        ..json('GET /organizations/b1/imports/i4/records', {
          'import_id': 'i4',
          'counts': {'sales': 90},
        })
        ..json('POST /organizations/b1/imports/i4/undo', {
          'data_import': importJson('i4', status: 'undone'),
          'removed': {'sales': 90},
        });
      await openHistory(tester, server);
      await tester.tap(find.text('march.csv'));
      await tester.pumpAndSettle();
      expect(find.text('Added: 90 sales.'), findsOneWidget);
      await tester.tap(find.text('Undo this import'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Undo it'));
      await tester.pumpAndSettle();
      expect(find.text('Removed: 90 sales.'), findsOneWidget);
    });

    testWidgets('one that was undone or failed is only explained', (
      tester,
    ) async {
      final server = dataServer()
        ..json(
          'GET /organizations/b1/imports/i5',
          uploadedJson('i5', status: 'undone'),
        )
        ..json(
          'GET /organizations/b1/imports/i6',
          uploadedJson('i6', status: 'failed'),
        );
      await openHistory(tester, server);
      await tester.tap(find.text('old.csv'));
      await tester.pumpAndSettle();
      expect(find.text('This import was undone'), findsOneWidget);
      await tester.pageBack();
      await tester.pumpAndSettle();
      await tester.tap(find.text('broken.csv'));
      await tester.pumpAndSettle();
      expect(
        find.textContaining(
          'This import did not finish. Nothing was added to your data.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('one that cannot be read says why', (tester) async {
      final server = dataServer()
        ..on(
          'GET /organizations/b1/imports/i1',
          (_) => errorResponse(
            404,
            'import_not_found',
            'That upload was not found.',
          ),
        );
      await openHistory(tester, server);
      await tester.tap(find.text('sales.csv'));
      await tester.pumpAndSettle();
      expect(find.text('That upload was not found.'), findsOneWidget);
    });
  });
}

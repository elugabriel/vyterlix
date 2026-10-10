import 'package:intl/intl.dart';

/// UK wording and numbers throughout: pounds, day/month/year, English month names.

final _pounds = NumberFormat.currency(
  locale: 'en_GB',
  symbol: '£',
  decimalDigits: 2,
);
final _plain = NumberFormat('#,##0.##', 'en_GB');
final _oneDecimal = NumberFormat('#,##0.0', 'en_GB');
final _twoDecimals = NumberFormat('#,##0.00', 'en_GB');

const _months = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December', //
];

/// "1234.5" (the server sends exact decimals as text) becomes "£1,234.50".
String gbp(String value) => _pounds.format(double.parse(value));

/// A figure in its own unit, as the website writes it: £1,234.50, 60.0%, 2.50 times, 15,864.
String formatValue(String? value, String unit) {
  if (value == null) return '–';
  switch (unit) {
    case 'gbp':
      return gbp(value);
    case 'percent':
      return '${_oneDecimal.format(double.parse(value))}%';
    case 'ratio':
      return '${_twoDecimals.format(double.parse(value))} times';
    default:
      return _plain.format(double.parse(value));
  }
}

/// "2026-09-01" becomes "September 2026".
String monthName(String isoDate) {
  final parts = isoDate.split('-');
  return '${_months[int.parse(parts[1]) - 1]} ${parts[0]}';
}

/// "2027-03-31" becomes "31/03/2027".
String ukDate(String isoDate) {
  final parts = isoDate.substring(0, 10).split('-');
  return '${parts[2]}/${parts[1]}/${parts[0]}';
}

String _two(int n) => n.toString().padLeft(2, '0');

/// "2026-10-09T12:00:00+00:00" becomes "09/10/2026, 13:00" in the phone's own time (UK time for a
/// phone set to the UK).
String ukDateTime(String iso) {
  final t = DateTime.parse(iso).toLocal();
  return '${_two(t.day)}/${_two(t.month)}/${t.year}, ${_two(t.hour)}:${_two(t.minute)}';
}

/// What a plain-English sentence for a change since the period before says, and which way it went
/// (1 up, -1 down, 0 none): "up 9.1% on the month before". A percentage figure is compared in points.
({int sign, String text})? changeSentence({
  required String status,
  required String? value,
  required String? previousValue,
  required double? changePct,
  required String unit,
}) {
  if (status != 'ok' || previousValue == null || value == null) return null;
  const before = 'the month before';
  if (unit == 'percent') {
    final points = double.parse(value) - double.parse(previousValue);
    if (points == 0) return (sign: 0, text: 'no change on $before');
    return (
      sign: points > 0 ? 1 : -1,
      text:
          '${points > 0 ? 'up' : 'down'} ${points.abs().toStringAsFixed(1)} points on $before',
    );
  }
  if (changePct == null) {
    return (sign: 0, text: 'was ${formatValue(previousValue, unit)} $before');
  }
  if (changePct == 0) return (sign: 0, text: 'no change on $before');
  return (
    sign: changePct > 0 ? 1 : -1,
    text:
        '${changePct > 0 ? 'up' : 'down'} ${changePct.abs().toStringAsFixed(1)}% on $before',
  );
}

const _needs = {
  'sales': 'sales',
  'expenses': 'expenses',
  'customer_sales': 'sales that say which customer bought',
  'stock': 'stock records',
};

/// Why a figure shows no number, in plain words.
String missingText(String status, List<String> requires) {
  if (status == 'no_data') {
    final needs = [for (final r in requires) _needs[r] ?? r];
    return needs.isEmpty
        ? "Needs records you haven't added yet."
        : "Needs ${needs.join(' and ')}, which you haven't added yet.";
  }
  return "Can't be worked out for this period (for example, nothing was sold, or there is no earlier period to compare with).";
}

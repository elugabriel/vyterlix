import 'package:intl/intl.dart';

/// UK wording and numbers throughout: pounds, day/month/year, English month names.

final _pounds = NumberFormat.currency(
  locale: 'en_GB',
  symbol: '£',
  decimalDigits: 2,
);
final _plain = NumberFormat('#,##0.##', 'en_GB');

const _months = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December', //
];

/// "1234.5" (the server sends exact decimals as text) becomes "£1,234.50".
String gbp(String value) => _pounds.format(double.parse(value));

/// A figure in its own unit: £, %, or a plain count.
String formatValue(String? value, String unit) {
  if (value == null) return '–';
  switch (unit) {
    case 'gbp':
      return gbp(value);
    case 'percent':
      return '${_plain.format(double.parse(value))}%';
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

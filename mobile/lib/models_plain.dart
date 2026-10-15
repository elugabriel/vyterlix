/// What a figure means, in everyday words (the server writes these; see app/plain/figures.py).
class PlainFigure {
  const PlainFigure({
    required this.says,
    required this.why,
    required this.tone,
    required this.caution,
  });

  static PlainFigure? from(dynamic json) {
    if (json is! Map<String, dynamic>) return null;
    return PlainFigure(
      says: '${json['says']}',
      why: '${json['why']}',
      tone: '${json['tone']}',
      caution: json['caution'] == null ? null : '${json['caution']}',
    );
  }

  /// What happened, one sentence.
  final String says;

  /// Why it matters.
  final String why;

  /// good, bad, watch, neutral or unknown
  final String tone;

  /// Set when the data behind the figure is incomplete.
  final String? caution;
}

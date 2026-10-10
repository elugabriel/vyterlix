import 'package:flutter/material.dart';

import '../theme.dart';

/// How serious something is, in words and in colour (the same on every screen).
const severityText = {
  'info': 'For information',
  'low': 'Low',
  'medium': 'Medium',
  'high': 'High',
  'critical': 'Critical',
};

Color severityColor(String severity) {
  switch (severity) {
    case 'critical':
    case 'high':
      return Palette.bad;
    case 'medium':
      return Palette.warn;
    default:
      return Palette.lightMuted;
  }
}

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

const healthText = {
  'healthy': 'Healthy',
  'fair': 'Fair',
  'needs_attention': 'Needs attention',
  'at_risk': 'At risk',
  'not_enough_data': 'Not enough data',
};

Color healthColor(String status) {
  switch (status) {
    case 'healthy':
      return Palette.ok;
    case 'fair':
      return Palette.warn;
    case 'needs_attention':
      return const Color(0xFFC2410C);
    case 'at_risk':
      return Palette.bad;
    default:
      return Palette.lightMuted;
  }
}

import 'package:flutter/material.dart';

/// The same look as the website: soft background, white rounded cards, one indigo accent.
class Palette {
  static const accent = Color(0xFF4F46E5);
  static const accent2 = Color(0xFF7C3AED);
  static const ok = Color(0xFF059669);
  static const warn = Color(0xFFD97706);
  static const bad = Color(0xFFDC2626);

  static const lightBackground = Color(0xFFF4F6FB);
  static const lightBorder = Color(0xFFE4E8F1);
  static const lightMuted = Color(0xFF64748B);

  static const gradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF3730A3), Color(0xFF6D28D9)],
  );
}

ThemeData buildTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final scheme = ColorScheme.fromSeed(
    seedColor: Palette.accent,
    brightness: brightness,
  ).copyWith(primary: dark ? const Color(0xFF818CF8) : Palette.accent);
  final border = dark ? const Color(0xFF212B45) : Palette.lightBorder;
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: dark
        ? const Color(0xFF0A0F1E)
        : Palette.lightBackground,
    cardTheme: CardThemeData(
      elevation: 0,
      margin: EdgeInsets.zero,
      color: dark ? const Color(0xFF121A2E) : Colors.white,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: border),
      ),
    ),
    appBarTheme: AppBarTheme(
      backgroundColor: dark ? const Color(0xFF121A2E) : Colors.white,
      foregroundColor: dark ? const Color(0xFFE8EBF3) : const Color(0xFF0F172A),
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      scrolledUnderElevation: 0,
      shape: Border(bottom: BorderSide(color: border)),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: dark ? const Color(0xFF121A2E) : Colors.white,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: border),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(
          color: dark ? const Color(0xFF2E3A58) : const Color(0xFFD3D9E6),
        ),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: scheme.primary, width: 2),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size.fromHeight(50),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        side: BorderSide(
          color: dark ? const Color(0xFF2E3A58) : const Color(0xFFD3D9E6),
        ),
      ),
    ),
    textTheme: Typography.material2021(platform: TargetPlatform.android).black
        .apply(
          bodyColor: dark ? const Color(0xFFE8EBF3) : const Color(0xFF0F172A),
          displayColor: dark
              ? const Color(0xFFE8EBF3)
              : const Color(0xFF0F172A),
        ),
  );
}

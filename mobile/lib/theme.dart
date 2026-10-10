import 'package:flutter/material.dart';

/// The same look as the website: soft background, white rounded cards, one green accent, with tea green as the soft colour.
class Palette {
  /// Tea green: the soft colour (selected tabs, highlights, soft backgrounds).
  static const tea = Color(0xFFD0F0C0);
  static const accent = Color(0xFF2F7D52);
  static const accent2 = Color(0xFF1F6B4A);
  static const ok = Color(0xFF059669);
  static const warn = Color(0xFFD97706);
  static const bad = Color(0xFFDC2626);

  static const lightBackground = Color(0xFFF3F9EF);
  static const lightBorder = Color(0xFFDBE8D3);
  static const lightMuted = Color(0xFF5B6F64);

  static const gradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF17492F), Color(0xFF2F7D52)],
  );
}

ThemeData buildTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final scheme = ColorScheme.fromSeed(
    seedColor: Palette.accent,
    brightness: brightness,
  ).copyWith(primary: dark ? const Color(0xFF3FA575) : Palette.accent);
  final border = dark ? const Color(0xFF1E3328) : Palette.lightBorder;
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: dark
        ? const Color(0xFF0A1510)
        : Palette.lightBackground,
    navigationBarTheme: NavigationBarThemeData(
      indicatorColor: dark ? const Color(0xFF1E3328) : Palette.tea,
    ),
    chipTheme: ChipThemeData(
      selectedColor: dark ? const Color(0xFF1E3328) : Palette.tea,
    ),
    cardTheme: CardThemeData(
      elevation: 0,
      margin: EdgeInsets.zero,
      color: dark ? const Color(0xFF11211A) : Colors.white,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: border),
      ),
    ),
    appBarTheme: AppBarTheme(
      backgroundColor: dark ? const Color(0xFF11211A) : Colors.white,
      foregroundColor: dark ? const Color(0xFFE6F0EA) : const Color(0xFF12241B),
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      scrolledUnderElevation: 0,
      shape: Border(bottom: BorderSide(color: border)),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: dark ? const Color(0xFF11211A) : Colors.white,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: border),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(
          color: dark ? const Color(0xFF2B4638) : const Color(0xFFC6D9BB),
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
          color: dark ? const Color(0xFF2B4638) : const Color(0xFFC6D9BB),
        ),
      ),
    ),
    textTheme: Typography.material2021(platform: TargetPlatform.android).black
        .apply(
          bodyColor: dark ? const Color(0xFFE6F0EA) : const Color(0xFF12241B),
          displayColor: dark
              ? const Color(0xFFE6F0EA)
              : const Color(0xFF12241B),
        ),
  );
}

import 'package:flutter/material.dart';

enum FindBackColor {
  background,
  card,
  ink,
  muted,
  line,
  controlLine,
  recipeBackground,
  recipeInk,
  brand,
  soft,
  danger,
  accent,
  accentSoft,
  accentInk,
  onBrand,
  toast,
  readingBackground,
  failedBackground,
  failedBorder,
  productBackground,
  productInk,
  stack2,
  stack3,
}

class FindBackTheme {
  static const summaryStyle = TextStyle(
      fontFamily: 'Source Serif 4',
      fontFamilyFallback: ['Noto Sans Arabic'],
      fontSize: 15,
      height: 1.55);
  static const light = <FindBackColor, Color>{
    FindBackColor.background: Color(0xFFEDF7F5),
    FindBackColor.card: Color(0xFFFFFFFF),
    FindBackColor.ink: Color(0xFF0C2A28),
    FindBackColor.muted: Color(0xFF4A6664),
    FindBackColor.line: Color(0xFFD3E6E3),
    FindBackColor.controlLine: Color(0xFF6F918C),
    FindBackColor.recipeBackground: Color(0xFFFFF0D2),
    FindBackColor.recipeInk: Color(0xFF8A5200),
    FindBackColor.brand: Color(0xFF0F766E),
    FindBackColor.soft: Color(0xFFD5F0EC),
    FindBackColor.danger: Color(0xFFB42318),
    FindBackColor.accent: Color(0xFFE8A317),
    FindBackColor.accentSoft: Color(0xFFFFF0D2),
    FindBackColor.accentInk: Color(0xFF8A5200),
    FindBackColor.onBrand: Color(0xFFFFFFFF),
    FindBackColor.toast: Color(0xFF0C2A28),
    FindBackColor.readingBackground: Color(0xFFFFFBF2),
    FindBackColor.failedBackground: Color(0xFFFFF8F7),
    FindBackColor.failedBorder: Color(0xFFF0C9C4),
    FindBackColor.productBackground: Color(0xFFE6ECF6),
    FindBackColor.productInk: Color(0xFF2F4A7A),
    FindBackColor.stack2: Color(0xFFB9E3DD),
    FindBackColor.stack3: Color(0xFF8FD0C7),
  };
  static const dark = <FindBackColor, Color>{
    FindBackColor.background: Color(0xFF0C1017),
    FindBackColor.card: Color(0xFF141A23),
    FindBackColor.ink: Color(0xFFE8EDF4),
    FindBackColor.muted: Color(0xFF97A3B6),
    FindBackColor.line: Color(0xFF232C3A),
    FindBackColor.controlLine: Color(0xFF4F7A74),
    FindBackColor.recipeBackground: Color(0xFF3A2B0E),
    FindBackColor.recipeInk: Color(0xFFF5C768),
    FindBackColor.brand: Color(0xFF47C4B4),
    FindBackColor.soft: Color(0xFF15313A),
    FindBackColor.danger: Color(0xFFFF8A7D),
    FindBackColor.accent: Color(0xFFE8A317),
    FindBackColor.accentSoft: Color(0xFF3A2B0E),
    FindBackColor.accentInk: Color(0xFFF5C768),
    FindBackColor.onBrand: Color(0xFF04201D),
    FindBackColor.toast: Color(0xFF263041),
    FindBackColor.readingBackground: Color(0xFF1A1710),
    FindBackColor.failedBackground: Color(0xFF1F1413),
    FindBackColor.failedBorder: Color(0xFF4A2622),
    FindBackColor.productBackground: Color(0xFF1E2D47),
    FindBackColor.productInk: Color(0xFFAFC7F2),
    FindBackColor.stack2: Color(0xFF1E5560),
    FindBackColor.stack3: Color(0xFF2B7B80),
  };

  static ThemeData build(Brightness brightness) {
    final colors = brightness == Brightness.dark ? dark : light;
    Color color(FindBackColor token) => colors[token]!;
    final scheme = ColorScheme.fromSeed(
            seedColor: color(FindBackColor.brand), brightness: brightness)
        .copyWith(
      primary: color(FindBackColor.brand),
      onPrimary: color(FindBackColor.onBrand),
      primaryContainer: color(FindBackColor.soft),
      onPrimaryContainer: color(FindBackColor.ink),
      secondary: color(FindBackColor.brand),
      onSecondary: color(FindBackColor.onBrand),
      secondaryContainer: color(FindBackColor.soft),
      onSecondaryContainer: color(FindBackColor.ink),
      surface: color(FindBackColor.card),
      onSurface: color(FindBackColor.ink),
      onSurfaceVariant: color(FindBackColor.muted),
      surfaceContainerLowest: color(FindBackColor.card),
      surfaceContainerLow: color(FindBackColor.card),
      surfaceContainer: color(FindBackColor.card),
      surfaceContainerHigh: color(FindBackColor.soft),
      surfaceContainerHighest: color(FindBackColor.soft),
      surfaceTint: Colors.transparent,
      outline: color(FindBackColor.controlLine),
      outlineVariant: color(FindBackColor.line),
      error: color(FindBackColor.danger),
    );
    final base = ThemeData(
        useMaterial3: true,
        colorScheme: scheme,
        fontFamily: 'Schibsted Grotesk',
        fontFamilyFallback: const ['Noto Sans Arabic']);
    final rounded =
        RoundedRectangleBorder(borderRadius: BorderRadius.circular(16));
    final button = ButtonStyle(
        minimumSize: const WidgetStatePropertyAll(Size(44, 44)),
        shape: WidgetStatePropertyAll(rounded),
        tapTargetSize: MaterialTapTargetSize.padded);
    return base.copyWith(
      scaffoldBackgroundColor: color(FindBackColor.background),
      textTheme: base.textTheme.copyWith(
        bodyLarge: base.textTheme.bodyLarge!.copyWith(fontSize: 15),
        bodyMedium: base.textTheme.bodyMedium!.copyWith(fontSize: 15),
        bodySmall: base.textTheme.bodySmall!
            .copyWith(fontSize: 12.5, color: color(FindBackColor.muted)),
        labelSmall: base.textTheme.labelSmall!.copyWith(fontSize: 12.5),
        labelMedium: base.textTheme.labelMedium!.copyWith(fontSize: 12.5),
      ),
      cardTheme: CardThemeData(
          color: color(FindBackColor.card),
          surfaceTintColor: Colors.transparent,
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(22),
              side: BorderSide(color: color(FindBackColor.line)))),
      appBarTheme: AppBarTheme(
          backgroundColor: color(FindBackColor.background),
          foregroundColor: color(FindBackColor.ink),
          scrolledUnderElevation: 0,
          titleTextStyle: TextStyle(
              fontFamily: 'Schibsted Grotesk',
              fontFamilyFallback: const ['Noto Sans Arabic'],
              fontSize: 22,
              fontWeight: FontWeight.w700,
              color: color(FindBackColor.ink))),
      navigationBarTheme:
          NavigationBarThemeData(backgroundColor: color(FindBackColor.card)),
      filledButtonTheme: FilledButtonThemeData(style: button),
      outlinedButtonTheme: OutlinedButtonThemeData(
          style: button.copyWith(
              side: WidgetStatePropertyAll(
                  BorderSide(color: color(FindBackColor.controlLine))))),
      textButtonTheme: TextButtonThemeData(style: button),
      iconButtonTheme: IconButtonThemeData(style: button),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: color(FindBackColor.card),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14)),
        enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
            borderSide: BorderSide(color: color(FindBackColor.controlLine))),
        focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
            borderSide:
                BorderSide(color: color(FindBackColor.brand), width: 2)),
      ),
      chipTheme: ChipThemeData(
          shape: const StadiumBorder(),
          backgroundColor: color(FindBackColor.card),
          selectedColor: color(FindBackColor.soft),
          side: BorderSide(color: color(FindBackColor.controlLine)),
          labelStyle: base.textTheme.labelLarge!
              .copyWith(color: color(FindBackColor.ink))),
      dialogTheme: DialogThemeData(
          backgroundColor: color(FindBackColor.card),
          surfaceTintColor: Colors.transparent,
          shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(22),
              side: BorderSide(color: color(FindBackColor.line)))),
      bottomSheetTheme: BottomSheetThemeData(
          backgroundColor: color(FindBackColor.card),
          surfaceTintColor: Colors.transparent,
          shape: const RoundedRectangleBorder(
              borderRadius: BorderRadius.vertical(top: Radius.circular(22)))),
      snackBarTheme: SnackBarThemeData(
          backgroundColor: color(FindBackColor.toast),
          contentTextStyle: base.textTheme.bodyMedium!
              .copyWith(color: const Color(0xFFE8EDF4)),
          actionTextColor: const Color(0xFFE8EDF4)),
    );
  }
}

package com.voiceshield.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.PlatformTextStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp

/**
 * Text metrics for a bilingual Tamil + English UI.
 *
 * Both values below are deliberately RELATIVE. GuardScreen overrides fontSize per call site
 * (up to 64.sp) without touching lineHeight, so an absolute lineHeight lays a large glyph out
 * in a short line box -- and Tamil, whose marks stack above AND below the base glyph, then
 * collides with the neighbouring line. Anything sized in em tracks the call site instead.
 */
private val Bilingual = TextStyle(
    fontFamily = FontFamily.Default,
    // 1.45 is sized for Tamil's stacked marks; the Latin-ish 1.2 leaves them touching.
    lineHeight = 1.45.em,
    // Compose drops the font's own ascent/descent padding by default. Tamil needs it, or the
    // topmost and bottommost marks clip against the edge of the line box.
    platformStyle = PlatformTextStyle(includeFontPadding = true),
    lineHeightStyle = LineHeightStyle(
        alignment = LineHeightStyle.Alignment.Center,
        trim = LineHeightStyle.Trim.None,
    ),
)

val Typography = Typography(
    // MaterialTheme installs bodyLarge as the default LocalTextStyle, so this covers every
    // plain Text in the app.
    bodyLarge = Bilingual.copy(
        fontWeight = FontWeight.Normal,
        fontSize = 16.sp,
        letterSpacing = 0.5.sp,
    ),
    // ...but NOT buttons: Material3's Button and TextButton wrap their content in
    // ProvideTextStyle(labelLarge), so bodyLarge never reaches a button label.
    labelLarge = Bilingual.copy(
        fontWeight = FontWeight.Medium,
        fontSize = 14.sp,
    ),
)

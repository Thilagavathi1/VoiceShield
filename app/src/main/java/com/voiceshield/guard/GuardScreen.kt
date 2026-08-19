package com.voiceshield.guard

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Elder-first UI. Design rules, all deliberate:
 *   - one control, impossible to miss
 *   - state readable across a room, at a glance, without reading text
 *   - colour is the primary signal, text is secondary, and both are redundant with the
 *     spoken warning and the haptic buzz. A person in the grip of a scam call is not
 *     reading a dashboard.
 *   - no risk numbers or debug detail in the elder's view; those go behind a long-press
 *     for the demo, so judges can see the machinery without cluttering the real UI.
 */
private val Calm = Color(0xFF0E7C4A)
private val Watching = Color(0xFF14532D)
private val Danger = Color(0xFFB3121B)
private val Degraded = Color(0xFF8A5200)
private val Ink = Color(0xFFFFFFFF)

@Composable
fun GuardScreen(
    ui: GuardUi,
    onStart: () -> Unit,
    onStop: () -> Unit,
    showDebug: Boolean = false,
    autoProtectOn: Boolean = false,
    onEnableAutoProtect: (() -> Unit)? = null,
) {
    val target = when (ui.state) {
        GuardState.ALERT -> Danger
        GuardState.WATCHING -> Watching
        // Amber, not green: listening but unable to judge. Deliberately not the
        // calm colour — the elder must not read this as protection.
        GuardState.DEGRADED -> Degraded
        GuardState.ERROR -> Color(0xFF4A148C)
        else -> Color(0xFF101418)
    }
    val bg by animateColorAsState(target, tween(220), label = "bg")

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(bg),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            when (ui.state) {
                GuardState.ALERT -> AlertBody(ui)
                GuardState.WATCHING -> Headline("கேட்டுக்கொண்டிருக்கிறேன்", "Listening — you are protected")
                // Never claims protection. Tells the elder plainly to be careful and
                // to call family, because right now nothing is checking the call.
                GuardState.DEGRADED -> Headline(
                    "பாதுகாப்பு இல்லை",
                    "Not protected right now — be careful, and call family before paying",
                )
                GuardState.STARTING -> Headline("தொடங்குகிறது…", "Starting")
                GuardState.ERROR -> Headline("சிக்கல்", ui.message ?: "Something went wrong")
                GuardState.IDLE -> Headline(
                    "VoiceShield",
                    if (autoProtectOn) {
                        "Protection is automatic — just answer the phone"
                    } else {
                        "Tap the button before you answer"
                    },
                )
            }

            Spacer(Modifier.height(48.dp))

            if (ui.state == GuardState.IDLE || ui.state == GuardState.ERROR) {
                BigButton("காப்பாற்றுங்க\nGUARD ME", Calm, onStart)
            } else {
                BigButton("நிறுத்துங்க\nSTOP", Color(0xFF37474F), onStop)
            }

            // Setup lives at the bottom of the idle screen because it is a one-time job for
            // the family member installing the app, not something the elder ever touches.
            if (ui.state == GuardState.IDLE && !autoProtectOn && onEnableAutoProtect != null) {
                Spacer(Modifier.height(32.dp))
                TextButton(onClick = onEnableAutoProtect) {
                    Text(
                        text = "Turn on automatic protection →",
                        color = Ink.copy(alpha = 0.85f),
                        fontSize = 17.sp,
                        textAlign = TextAlign.Center,
                    )
                }
            }

            if (showDebug) {
                Spacer(Modifier.height(28.dp))
                Text(
                    text = "risk ${ui.risk} · ${ui.pattern} · ${ui.latencyMs}ms · ${ui.trigger}" +
                        if (ui.signals.isEmpty()) "" else "\n${ui.signals.joinToString(" · ")}",
                    color = Ink.copy(alpha = 0.72f),
                    fontSize = 13.sp,
                    textAlign = TextAlign.Center,
                )
            }
        }
    }
}

@Composable
private fun AlertBody(ui: GuardUi) {
    val pulse = rememberInfiniteTransition(label = "pulse")
    val a by pulse.animateFloat(
        initialValue = 0.45f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(520), RepeatMode.Reverse),
        label = "alpha",
    )
    Box(
        Modifier
            .size(132.dp)
            .alpha(a)
            .background(Ink, CircleShape),
    )
    Spacer(Modifier.height(28.dp))
    Text(
        text = "நிற்குங்க!",
        color = Ink,
        fontSize = 64.sp,
        fontWeight = FontWeight.Black,
        textAlign = TextAlign.Center,
    )
    Text(
        text = "இது மோசடி. ஃபோனை வையுங்க.\nThis is a scam. Hang up.",
        color = Ink,
        fontSize = 26.sp,
        fontWeight = FontWeight.SemiBold,
        textAlign = TextAlign.Center,
        modifier = Modifier.padding(top = 12.dp),
    )
}

@Composable
private fun Headline(primary: String, secondary: String) {
    Text(
        text = primary,
        color = Ink,
        fontSize = 44.sp,
        fontWeight = FontWeight.Bold,
        textAlign = TextAlign.Center,
    )
    Text(
        text = secondary,
        color = Ink.copy(alpha = 0.78f),
        fontSize = 20.sp,
        textAlign = TextAlign.Center,
        modifier = Modifier.padding(top = 10.dp),
    )
}

@Composable
private fun BigButton(label: String, color: Color, onClick: () -> Unit) {
    Button(
        onClick = onClick,
        colors = ButtonDefaults.buttonColors(containerColor = color, contentColor = Ink),
        shape = CircleShape,
        modifier = Modifier.size(220.dp),
    ) {
        Text(
            text = label,
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
        )
    }
}

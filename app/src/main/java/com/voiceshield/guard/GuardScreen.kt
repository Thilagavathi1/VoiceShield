package com.voiceshield.guard

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.GppBad
import androidx.compose.material.icons.filled.GppGood
import androidx.compose.material.icons.filled.GppMaybe
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Elder-first UI. Design rules, all deliberate:
 *   - one control, impossible to miss
 *   - state readable across a room, at a glance, without reading text
 *   - colour is the primary signal, the shield icon is a second, redundant signal (protected
 *     / danger / uncertain reads from shape alone), text is third. All three plus the spoken
 *     warning and the haptic buzz say the same thing. A person in the grip of a scam call is
 *     not reading a dashboard.
 *   - English-only. An earlier bilingual build's Tamil headlines broke mid-word at display
 *     size (Compose text has no grapheme-aware line breaking for Tamil), and half a Tamil
 *     word reads as a different, wrong word -- worse than not having one. The backend still
 *     classifies and can speak the warning in Hindi, Tamil or English (see
 *     Verdict.warning_for() in backend/app/guard.py); only the on-screen chrome is English.
 *   - no risk numbers or debug detail in the elder's view; those go behind a long-press
 *     for the demo, so judges can see the machinery without cluttering the real UI.
 *
 * The visual language below (gradient field, ambient blobs, glossy button, icon-in-a-halo,
 * cross-fade between states) is stylistic, layered on top of those rules rather than
 * replacing them. None of it may touch: the single hue that signals each state, the
 * one-line headlines (still `maxLines = 1` with an `Ellipsis` safety net -- see the ERROR
 * headline history below), or the one-control layout.
 */
private val Calm = Color(0xFF0E7C4A)
private val Watching = Color(0xFF14532D)
private val Danger = Color(0xFFB3121B)
private val Degraded = Color(0xFF8A5200)
private val ErrorColor = Color(0xFF4A148C)
private val Idle = Color(0xFF0A0E12)
private val Ink = Color(0xFFFFFFFF)

/** Same hue, lightened toward the top-left and darkened toward the bottom-right -- reads as
 * one flat colour from across a room, but gives the full-bleed field real depth up close.
 * Diagonal rather than vertical, and a wider spread than a first pass, for a more deliberate
 * "designed" field. Never crosses hue, so it can't dilute the colour-as-signal rule. */
private fun Color.fieldGradient(): Brush = Brush.linearGradient(
    listOf(lighten(0.10f), this, darken(0.78f)),
)

private fun Color.lighten(factor: Float): Color = copy(
    red = red + (1f - red) * factor,
    green = green + (1f - green) * factor,
    blue = blue + (1f - blue) * factor,
)

private fun Color.darken(factor: Float): Color = copy(red = red * factor, green = green * factor, blue = blue * factor)

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
        GuardState.ERROR -> ErrorColor
        else -> Idle
    }
    val bg by animateColorAsState(target, tween(280), label = "bg")

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(bg.fieldGradient()),
        contentAlignment = Alignment.Center,
    ) {
        // Ambient blobs: purely atmospheric, drawn under the content column, same hue family
        // as the field so they read as depth, not decoration. Kept low-alpha so they never
        // compete with text contrast -- the colour-as-signal rule cares about the FIELD, not
        // these.
        Box(
            Modifier
                .align(Alignment.TopEnd)
                .offset(x = 90.dp, y = (-70).dp)
                .size(320.dp)
                .background(
                    Brush.radialGradient(listOf(bg.lighten(0.34f).copy(alpha = 0.30f), Color.Transparent)),
                    CircleShape,
                ),
        )
        Box(
            Modifier
                .align(Alignment.BottomStart)
                .offset(x = (-90).dp, y = 90.dp)
                .size(280.dp)
                .background(
                    Brush.radialGradient(listOf(bg.lighten(0.22f).copy(alpha = 0.22f), Color.Transparent)),
                    CircleShape,
                ),
        )

        Column(
            modifier = Modifier
                .fillMaxWidth()
                .safeDrawingPadding()
                .padding(28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            // Cross-fade + scale-in between states instead of an instant cut. The target is
            // just the enum -- ui.message/autoProtectOn are read from the outer closure, so
            // they don't need to be part of what's being animated between.
            AnimatedContent(
                targetState = ui.state,
                transitionSpec = {
                    (fadeIn(tween(260)) + scaleIn(initialScale = 0.94f, animationSpec = tween(260))) togetherWith
                        fadeOut(tween(150))
                },
                label = "state-body",
            ) { state ->
                when (state) {
                    GuardState.ALERT -> AlertBody()
                    GuardState.WATCHING -> StateBody(
                        icon = Icons.Filled.GppGood,
                        headline = "Listening",
                        subtitle = "You are protected",
                        // The mic really is live in this state -- a literal cue for what's
                        // actually happening, not just decoration.
                        badge = Icons.Filled.Mic,
                    )
                    GuardState.DEGRADED -> StateBody(
                        icon = Icons.Filled.GppMaybe,
                        headline = "Unprotected",
                        subtitle = "Be careful — call family before paying",
                        // Still listening here too -- the classifier just couldn't judge the
                        // last turn. The mic badge says so; the shield-maybe says why that
                        // doesn't mean protected.
                        badge = Icons.Filled.Mic,
                    )
                    GuardState.STARTING -> Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        CircularProgressIndicator(color = Ink, strokeWidth = 4.dp, modifier = Modifier.size(56.dp))
                        Spacer(Modifier.height(28.dp))
                        Text(
                            "Starting…",
                            color = Ink,
                            fontSize = 34.sp,
                            fontWeight = FontWeight.Bold,
                            letterSpacing = (-0.3).sp,
                        )
                    }
                    GuardState.ERROR -> StateBody(
                        // Short headline, message in the subtitle -- same split every other
                        // state uses. "Something Went Wrong" as the headline silently clipped
                        // the word "Wrong" at 38sp: maxLines = 1 has no ellipsis set, so text
                        // past the line width just disappears, unseen, instead of wrapping.
                        // Worse than the old Tamil mid-word break, because nothing looks wrong.
                        icon = Icons.Filled.ErrorOutline,
                        headline = "Error",
                        subtitle = ui.message ?: "Something went wrong. Please try again.",
                    )
                    GuardState.IDLE -> StateBody(
                        icon = Icons.Filled.Shield,
                        headline = "VoiceShield",
                        subtitle = if (autoProtectOn) {
                            "Protection is automatic — just answer the phone"
                        } else {
                            "Tap the button before you answer"
                        },
                        wordmark = true,
                    )
                }
            }

            Spacer(Modifier.height(48.dp))

            if (ui.state == GuardState.IDLE || ui.state == GuardState.ERROR) {
                BigButton("Guard Me", Icons.Filled.Shield, Calm, onStart, breathing = true)
            } else {
                BigButton("Stop", Icons.Filled.Stop, Color(0xFF37474F), onStop)
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
                Box(
                    Modifier
                        .background(Color.Black.copy(alpha = 0.30f), RoundedCornerShape(18.dp))
                        .padding(horizontal = 18.dp, vertical = 12.dp),
                ) {
                    Text(
                        // Capped at four: the Column is centred and does not scroll, so a
                        // six-signal verdict pushes the tail of the list off the bottom of
                        // the screen -- during a demo that reads as a rendering bug, not detail.
                        text = "risk ${ui.risk} · ${ui.pattern} · ${ui.latencyMs}ms · ${ui.trigger}" +
                            if (ui.signals.isEmpty()) {
                                ""
                            } else {
                                val shown = ui.signals.take(4).joinToString(" · ")
                                val extra = ui.signals.size - 4
                                "\n" + shown + if (extra > 0) " · +$extra more" else ""
                            },
                        color = Ink.copy(alpha = 0.85f),
                        fontSize = 13.sp,
                        textAlign = TextAlign.Center,
                    )
                }
            }
        }
    }
}

/** Icon in a soft tinted circle, with an optional small badge overlapping its bottom-right
 * edge -- a mic while the mic is genuinely live (WATCHING, DEGRADED: both are still
 * listening), a speaker while the warning is being spoken into the call (ALERT). The
 * shield-family icon underneath still carries protection status; the badge adds mechanism
 * on top of it, not a replacement -- "shield = status" stays true in every state. */
@Composable
private fun IconHalo(
    icon: ImageVector,
    haloSize: Dp,
    iconSize: Dp,
    badge: ImageVector? = null,
    badgePulses: Boolean = true,
    modifier: Modifier = Modifier,
) {
    Box(modifier = modifier, contentAlignment = Alignment.Center) {
        Box(
            modifier = Modifier
                .size(haloSize)
                .background(Ink.copy(alpha = 0.14f), CircleShape),
            contentAlignment = Alignment.Center,
        ) {
            Icon(icon, contentDescription = null, tint = Ink, modifier = Modifier.size(iconSize))
        }
        if (badge != null) {
            val badgeScale = if (badgePulses) {
                val transition = androidx.compose.animation.core.rememberInfiniteTransition(label = "badge-pulse")
                val scale by transition.animateFloat(
                    initialValue = 0.9f,
                    targetValue = 1.08f,
                    animationSpec = infiniteRepeatable(tween(700, easing = FastOutSlowInEasing), RepeatMode.Reverse),
                    label = "badge-scale",
                )
                scale
            } else {
                1f
            }
            Box(
                modifier = Modifier
                    .align(Alignment.BottomEnd)
                    .offset(x = (-4).dp, y = (-4).dp)
                    .size(36.dp)
                    .scale(badgeScale)
                    .background(Color.Black.copy(alpha = 0.55f), CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                Icon(badge, contentDescription = null, tint = Ink, modifier = Modifier.size(19.dp))
            }
        }
    }
}

/** Icon + headline + subtitle, the shape every non-alert state shares.
 *
 * Wrapped in its own Column deliberately: this is called from inside AnimatedContent's
 * content slot, which overlays its children rather than stacking them the way the outer
 * Column used to. Without this wrapper the icon, headline and subtitle render on top of
 * each other instead of in sequence -- caught on-device, not assumed. */
@Composable
private fun StateBody(
    icon: ImageVector,
    headline: String,
    subtitle: String,
    wordmark: Boolean = false,
    badge: ImageVector? = null,
) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        IconHalo(icon = icon, haloSize = 108.dp, iconSize = 60.dp, badge = badge)
        Spacer(Modifier.height(24.dp))
        Text(
            text = headline,
            color = Ink,
            // The wordmark gets a hair more size and tighter tracking than a status headline
            // — it is the one screen the elder sees when nothing is happening, and it should
            // read as a name, not a status. Both are one line: English never breaks mid-word
            // the way the earlier Tamil headline did, but maxLines still fails loudly if a
            // string grows.
            fontSize = if (wordmark) 44.sp else 38.sp,
            fontWeight = FontWeight.Bold,
            letterSpacing = if (wordmark) (-0.8).sp else (-0.4).sp,
            textAlign = TextAlign.Center,
            maxLines = 1,
            // A safety net, not the fix: every headline here is chosen to fit at this size,
            // but if a future edit grows one past the line width, this makes it visibly
            // truncate ("…") instead of silently dropping the tail -- which is what maxLines
            // alone did to "Something Went Wrong" here, unnoticed until this build was
            // screenshotted.
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            text = subtitle,
            color = Ink.copy(alpha = 0.80f),
            fontSize = 19.sp,
            letterSpacing = 0.2.sp,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = 10.dp),
        )
    }
}

@Composable
private fun AlertBody() {
    val pulse = rememberInfiniteAlpha()
    // Same reason as StateBody's Column wrapper: a direct child of AnimatedContent's content
    // slot, so it needs its own layout container or its three pieces overlay instead of stack.
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        // The speaker badge marks the moment the warning is actually going out over the
        // call -- the /speak INTERRUPT this whole screen exists for. badgePulses = false:
        // the parent alpha pulse already animates this whole icon, and layering the badge's
        // own scale pulse on top of that made the busiest screen in the app busier still.
        IconHalo(
            icon = Icons.Filled.GppBad,
            haloSize = 148.dp,
            iconSize = 88.dp,
            badge = Icons.AutoMirrored.Filled.VolumeUp,
            badgePulses = false,
            modifier = Modifier.alpha(pulse),
        )
        Spacer(Modifier.height(20.dp))
        Text(
            // Same word as the STOP button, deliberately. This screen and that button ask
            // for one single action, and an elder reading two different words for it
            // mid-scam has to stop and work out whether they mean the same thing.
            text = "STOP",
            color = Ink,
            fontSize = 52.sp,
            fontWeight = FontWeight.Black,
            letterSpacing = (-0.6).sp,
            textAlign = TextAlign.Center,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            text = "This is a scam. Hang up now.",
            color = Ink,
            fontSize = 24.sp,
            fontWeight = FontWeight.SemiBold,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(top = 10.dp),
        )
    }
}

@Composable
private fun rememberInfiniteAlpha(): Float {
    val pulse = androidx.compose.animation.core.rememberInfiniteTransition(label = "pulse")
    val a by pulse.animateFloat(
        initialValue = 0.45f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(520), RepeatMode.Reverse),
        label = "alpha",
    )
    return a
}

@Composable
private fun BigButton(
    label: String,
    icon: ImageVector,
    color: Color,
    onClick: () -> Unit,
    breathing: Boolean = false,
) {
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()
    val pressScale by animateFloatAsState(if (pressed) 0.94f else 1f, label = "press-scale")

    // A slow, small breathe on the one primary call-to-action (Guard Me on the idle screen)
    // so the eye is drawn to it without being distracting -- there is only ever one control,
    // and this is that control asking to be tapped.
    val breatheScale = if (breathing) {
        val transition = androidx.compose.animation.core.rememberInfiniteTransition(label = "breathe")
        val scale by transition.animateFloat(
            initialValue = 1f,
            targetValue = 1.035f,
            animationSpec = infiniteRepeatable(tween(1400, easing = FastOutSlowInEasing), RepeatMode.Reverse),
            label = "breathe-scale",
        )
        scale
    } else {
        1f
    }

    Box(contentAlignment = Alignment.Center) {
        // A soft halo behind the button, same colour, low alpha -- the button reads as the
        // centre of a glow rather than a flat disc dropped on the field.
        Box(
            Modifier
                .size(272.dp)
                .background(color.copy(alpha = 0.16f), CircleShape),
        )
        Button(
            onClick = onClick,
            interactionSource = interaction,
            // Transparent container: the visible fill is the gradient background modifier
            // below, so the circle gets a glossy top-to-bottom sheen instead of one flat
            // tone. Button still owns touch target, ripple and disabled-state handling.
            colors = ButtonDefaults.buttonColors(containerColor = Color.Transparent, contentColor = Ink),
            shape = CircleShape,
            contentPadding = PaddingValues(12.dp),
            modifier = Modifier
                .size(232.dp)
                .scale(pressScale * breatheScale)
                .shadow(elevation = 26.dp, shape = CircleShape, ambientColor = color, spotColor = color)
                .background(
                    Brush.verticalGradient(listOf(color.lighten(0.20f), color, color.darken(0.82f))),
                    CircleShape,
                ),
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Icon(icon, contentDescription = null, modifier = Modifier.size(40.dp))
                Spacer(Modifier.height(6.dp))
                Text(
                    text = label,
                    fontSize = 22.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = (-0.2).sp,
                    textAlign = TextAlign.Center,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

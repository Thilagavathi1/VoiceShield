package com.voiceshield.guard

import android.app.Application
import android.content.Context
import android.os.Build
import android.os.CombinedVibration
import android.os.VibrationEffect
import android.os.VibratorManager
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.launch
import kotlin.random.Random

enum class GuardState {
    IDLE,
    STARTING,
    WATCHING,
    ALERT,
    /**
     * The session is live but the classifier could not judge the last turn. We are
     * listening and NOT protecting. This state exists so the app never shows a
     * protection claim it cannot back — a safety device that fails silently is worse
     * than no device, because it supplies exactly the false confidence a scammer needs.
     */
    DEGRADED,
    ERROR,
}

/** How this guard session was started. Surfaced in the UI so the demo can prove auto-arm. */
enum class Trigger { MANUAL, UNKNOWN_CALLER }

data class GuardUi(
    val state: GuardState = GuardState.IDLE,
    val risk: Int = 0,
    val pattern: String = "none",
    val signals: List<String> = emptyList(),
    val latencyMs: Int = 0,
    // ta-IN, matching the Tamil UI and the backend's own DEFAULT_LANGUAGE. This value is
    // what Agora's ASR is configured with, and nothing ever calls setLanguage(), so the
    // default IS the language for every session -- leaving it at hi-IN transcribed Tamil
    // speech through a Hindi model and fed the classifier noise.
    val language: String = "ta-IN",
    val trigger: Trigger = Trigger.MANUAL,
    val message: String? = null,
)

/**
 * Owns the guard session, process-wide.
 *
 * This is a singleton rather than ViewModel state for one reason: the guard is started by
 * things that are not the UI. A call from an unknown number arms it from a
 * CallScreeningService / broadcast receiver, with no Activity alive at all. State that
 * lives in a ViewModel cannot be driven from there.
 *
 * Both GuardViewModel and GuardService are thin shells over this.
 */
object GuardController {

    private const val TAG = "VoiceShieldGuard"
    private const val WARN_AT = 70

    private val backend = BackendClient()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    private var rtc: RtcGuard? = null
    private var riskJob: Job? = null

    private val _ui = MutableStateFlow(GuardUi())
    val ui: StateFlow<GuardUi> = _ui.asStateFlow()

    var channel: String? = null
        private set

    val isActive: Boolean
        get() = _ui.value.state.let {
            it == GuardState.STARTING || it == GuardState.WATCHING || it == GuardState.ALERT
        }

    fun setLanguage(tag: String) {
        _ui.value = _ui.value.copy(language = tag)
    }

    fun start(context: Context, trigger: Trigger = Trigger.MANUAL) {
        if (isActive) {
            Log.i(TAG, "guard already active, ignoring $trigger start")
            return
        }

        val ch = "vs-${Random.nextInt(100_000, 999_999)}"
        channel = ch
        _ui.value = _ui.value.copy(
            state = GuardState.STARTING,
            trigger = trigger,
            risk = 0,
            pattern = "none",
            signals = emptyList(),
            message = null,
        )
        Log.i(TAG, "starting guard on $ch via $trigger")

        val appContext = context.applicationContext
        scope.launch {
            try {
                val session = backend.startSession(ch, _ui.value.language)
                rtc = RtcGuard(
                    context = appContext,
                    onError = { msg ->
                        _ui.value = _ui.value.copy(state = GuardState.ERROR, message = msg)
                    },
                ).also { it.start(session) }

                _ui.value = _ui.value.copy(state = GuardState.WATCHING)
                observeRisk(appContext, ch)
            } catch (e: Exception) {
                Log.e(TAG, "could not start guard", e)
                _ui.value = _ui.value.copy(
                    state = GuardState.ERROR,
                    message = e.message ?: "could not start guard",
                )
            }
        }
    }

    private fun observeRisk(context: Context, ch: String) {
        riskJob?.cancel()
        riskJob = scope.launch {
            backend.riskUpdates(ch)
                .catch { e ->
                    _ui.value = _ui.value.copy(
                        state = GuardState.ERROR,
                        message = "lost connection to guard: ${e.message}",
                    )
                }
                .collect { update -> apply(context, update) }
        }
    }

    private fun apply(context: Context, u: RiskUpdate) {
        if (!u.usable) {
            // Could not judge this turn. Say so rather than implying protection.
            Log.w(TAG, "guard degraded: ${u.error}")
            _ui.value = _ui.value.copy(
                state = GuardState.DEGRADED,
                risk = 0,
                pattern = "unjudged",
                signals = emptyList(),
                latencyMs = u.latencyMs,
                message = u.error,
            )
            return
        }

        val prev = _ui.value
        val wasAlerting = prev.state == GuardState.ALERT
        val alerting = u.warned || u.risk >= WARN_AT
        // The alert is latched server-side, so turns keep arriving after the warning fires --
        // and on a real call the next thing the scammer says is usually bland. Keep the
        // verdict that actually tripped the alarm on screen as one consistent row, instead of
        // letting "risk 0 / none / 0ms" sit under a red screen and contradict it.
        val supersede = !alerting || u.risk >= prev.risk
        _ui.value = prev.copy(
            state = if (alerting) GuardState.ALERT else GuardState.WATCHING,
            risk = if (supersede) u.risk else prev.risk,
            pattern = if (supersede) u.pattern else prev.pattern,
            signals = if (supersede) u.signals else prev.signals,
            latencyMs = if (supersede) u.latencyMs else prev.latencyMs,
            message = null,
        )
        // The spoken warning is the primary channel; haptics are for the hard-of-hearing,
        // which is a large fraction of the people this app exists for.
        //
        // Only on a NEW escalation. Buzzing on every latched update means buzzing once per
        // turn for the rest of the call, which stops being a signal and becomes noise the
        // elder wants to silence -- the opposite of what we want them to do.
        if (alerting && (!wasAlerting || u.risk > prev.risk)) buzz(context)
    }

    fun stop(context: Context) {
        val ch = channel
        riskJob?.cancel()
        riskJob = null
        rtc?.stop()
        rtc = null
        channel = null
        if (ch != null) {
            scope.launch { backend.stopSession(ch) }
        }
        _ui.value = GuardUi(language = _ui.value.language)
    }

    private fun buzz(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return
        val manager = context.getSystemService(VibratorManager::class.java) ?: return
        val pattern = longArrayOf(0, 400, 200, 400, 200, 400)
        manager.vibrate(
            CombinedVibration.createParallel(
                VibrationEffect.createWaveform(pattern, -1)
            )
        )
    }
}

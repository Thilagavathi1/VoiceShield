package com.voiceshield.guard

import android.app.Application
import android.os.Build
import android.os.CombinedVibration
import android.os.VibrationEffect
import android.os.VibratorManager
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.launch
import kotlin.random.Random

enum class GuardState { IDLE, STARTING, WATCHING, ALERT, ERROR }

data class GuardUi(
    val state: GuardState = GuardState.IDLE,
    val risk: Int = 0,
    val pattern: String = "none",
    val signals: List<String> = emptyList(),
    val latencyMs: Int = 0,
    val language: String = "hi-IN",
    val message: String? = null,
)

class GuardViewModel(app: Application) : AndroidViewModel(app) {

    private val backend = BackendClient()
    private var rtc: RtcGuard? = null
    private var riskJob: Job? = null
    private var channel: String? = null

    private val _ui = MutableStateFlow(GuardUi())
    val ui: StateFlow<GuardUi> = _ui.asStateFlow()

    fun setLanguage(tag: String) {
        _ui.value = _ui.value.copy(language = tag)
    }

    fun startGuard() {
        if (_ui.value.state == GuardState.STARTING ||
            _ui.value.state == GuardState.WATCHING
        ) return

        val ch = "voiceshield-${Random.nextInt(100_000, 999_999)}"
        channel = ch
        _ui.value = _ui.value.copy(state = GuardState.STARTING, message = null)

        viewModelScope.launch {
            try {
                val session = backend.startSession(ch, _ui.value.language)
                rtc = RtcGuard(
                    context = getApplication(),
                    onError = { msg ->
                        _ui.value = _ui.value.copy(state = GuardState.ERROR, message = msg)
                    },
                ).also { it.start(session) }

                _ui.value = _ui.value.copy(state = GuardState.WATCHING)
                observeRisk(ch)
            } catch (e: Exception) {
                _ui.value = _ui.value.copy(
                    state = GuardState.ERROR,
                    message = e.message ?: "could not start guard",
                )
            }
        }
    }

    private fun observeRisk(ch: String) {
        riskJob?.cancel()
        riskJob = viewModelScope.launch {
            backend.riskUpdates(ch)
                .catch { e ->
                    _ui.value = _ui.value.copy(
                        state = GuardState.ERROR,
                        message = "lost connection to guard: ${e.message}",
                    )
                }
                .collect { update -> apply(update) }
        }
    }

    private fun apply(u: RiskUpdate) {
        val alerting = u.warned || u.risk >= WARN_AT
        _ui.value = _ui.value.copy(
            state = if (alerting) GuardState.ALERT else GuardState.WATCHING,
            risk = u.risk,
            pattern = u.pattern,
            signals = u.signals,
            latencyMs = u.latencyMs,
        )
        // The spoken warning is the primary channel; haptics are for the hard-of-hearing,
        // which is a large fraction of the people this app exists for.
        if (alerting) buzz()
    }

    fun stopGuard() {
        val ch = channel ?: return
        riskJob?.cancel()
        rtc?.stop()
        rtc = null
        viewModelScope.launch { backend.stopSession(ch) }
        channel = null
        _ui.value = GuardUi(language = _ui.value.language)
    }

    private fun buzz() {
        val ctx = getApplication<Application>()
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return
        val manager = ctx.getSystemService(VibratorManager::class.java) ?: return
        val pattern = longArrayOf(0, 400, 200, 400, 200, 400)
        manager.vibrate(
            CombinedVibration.createParallel(
                VibrationEffect.createWaveform(pattern, -1)
            )
        )
    }

    override fun onCleared() {
        rtc?.stop()
        super.onCleared()
    }

    private companion object {
        const val WARN_AT = 70
    }
}

package com.voiceshield.guard

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import kotlinx.coroutines.flow.StateFlow

/**
 * Thin shell over [GuardController].
 *
 * All real state lives in the controller because the guard can be started with no UI
 * present at all (see ScamCallScreeningService). The ViewModel exists only so Compose has
 * something lifecycle-scoped to collect.
 */
class GuardViewModel(app: Application) : AndroidViewModel(app) {

    val ui: StateFlow<GuardUi> = GuardController.ui

    fun setLanguage(tag: String) = GuardController.setLanguage(tag)

    fun startGuard() = GuardService.start(getApplication(), Trigger.MANUAL)

    fun stopGuard() = GuardService.stop(getApplication())
}

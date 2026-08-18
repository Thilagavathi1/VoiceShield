package com.voiceshield.guard.call

import android.telecom.Call
import android.telecom.CallScreeningService
import android.util.Log

/**
 * Sees every incoming call before it rings, and decides whether the guard should arm.
 *
 * This is the step that answers "why would an elderly user remember to do this?" — they
 * don't. The system hands us the number before the phone even rings, and we arm silently.
 *
 * Requires the user to grant ROLE_CALL_SCREENING once during setup, which in practice is
 * done by the family member who installs the app, not the person being protected.
 *
 * Note what we deliberately do NOT do: block, reject, or silence the call. VoiceShield's
 * whole thesis is that the elder stays in control and simply gets told the truth in time.
 * Silently dropping calls would make the app dangerous in the other direction — a missed
 * hospital call is also a harm.
 */
class ScamCallScreeningService : CallScreeningService() {

    override fun onScreenCall(callDetails: Call.Details) {
        val incoming = callDetails.callDirection == Call.Details.DIRECTION_INCOMING
        val number = callDetails.handle?.schemeSpecificPart

        if (incoming && CallGuard.shouldGuard(this, number)) {
            CallGuard.arm(number)
            // Offer the one-tap path immediately, while the phone is still ringing. If the
            // dialer role is also granted the guard starts on its own when they answer;
            // this notification is the fallback for when it is not.
            CallAlerts.showArmedWhileRinging(this, number)
        } else {
            CallGuard.disarm()
        }

        // Always let the call through, untouched.
        respondToCall(callDetails, CallResponse.Builder().build())
    }

    companion object {
        private const val TAG = "VoiceShieldScreening"
    }
}

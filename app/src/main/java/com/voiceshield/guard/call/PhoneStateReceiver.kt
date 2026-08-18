package com.voiceshield.guard.call

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telephony.TelephonyManager
import android.util.Log
import com.voiceshield.guard.GuardService
import com.voiceshield.guard.Trigger

/**
 * Starts the guard the moment an armed call is actually answered, and tears it down when
 * the call ends.
 *
 * We start on OFFHOOK rather than on RINGING on purpose: arming during the ring would burn
 * an Agora session on every unanswered spam call, and there is nothing to listen to until
 * somebody picks up.
 *
 * PHONE_STATE is one of the broadcasts exempt from the implicit-broadcast restrictions, so
 * a manifest-registered receiver still works with the app not running — which is the whole
 * point.
 */
class PhoneStateReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != TelephonyManager.ACTION_PHONE_STATE_CHANGED) return

        when (intent.getStringExtra(TelephonyManager.EXTRA_STATE)) {
            TelephonyManager.EXTRA_STATE_OFFHOOK -> {
                if (CallGuard.isArmed()) {
                    Log.i(TAG, "armed call answered -> starting guard")
                    CallAlerts.dismissArmed(context)
                    GuardService.start(context, Trigger.UNKNOWN_CALLER)
                }
            }

            TelephonyManager.EXTRA_STATE_IDLE -> {
                // Call over: stop the guard and clear the arm, whatever started it.
                CallAlerts.dismissArmed(context)
                CallGuard.disarm()
                GuardService.stop(context)
            }
        }
    }

    private companion object {
        const val TAG = "VoiceShieldPhoneState"
    }
}

package com.voiceshield.guard.call

import android.app.role.RoleManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log

/**
 * One-time setup for automatic protection.
 *
 * In the real world this is done by the son or daughter who installs the app, not by the
 * person being protected. That is the product answer to "why would an elderly user
 * remember to enable this?" — they never do it at all.
 */
object CallScreeningSetup {

    private const val TAG = "VoiceShieldSetup"

    /** ROLE_CALL_SCREENING arrived in API 29; below that only the manual button works. */
    fun isSupported(): Boolean = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q

    fun isGranted(context: Context): Boolean {
        if (!isSupported()) return false
        val rm = context.getSystemService(RoleManager::class.java) ?: return false
        return rm.isRoleAvailable(RoleManager.ROLE_CALL_SCREENING) &&
            rm.isRoleHeld(RoleManager.ROLE_CALL_SCREENING)
    }

    /**
     * Intent that asks the user to make VoiceShield the call screening app.
     * Returns null when unsupported or already granted, so callers can hide the button.
     */
    fun requestIntent(context: Context): Intent? {
        if (!isSupported()) return null
        val rm = context.getSystemService(RoleManager::class.java) ?: return null
        if (!rm.isRoleAvailable(RoleManager.ROLE_CALL_SCREENING)) {
            Log.i(TAG, "call screening role unavailable on this device")
            return null
        }
        if (rm.isRoleHeld(RoleManager.ROLE_CALL_SCREENING)) return null
        return rm.createRequestRoleIntent(RoleManager.ROLE_CALL_SCREENING)
    }
}

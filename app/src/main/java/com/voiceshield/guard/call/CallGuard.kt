package com.voiceshield.guard.call

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.ContactsContract
import android.util.Log
import androidx.core.content.ContextCompat

/**
 * Decides whether an incoming call deserves the guard, and remembers that decision until
 * the call is answered.
 *
 * Split across two Android entry points by necessity:
 *   - CallScreeningService learns the number, but is not told when the call is answered.
 *   - The PHONE_STATE broadcast learns when the call is answered, but on modern Android is
 *     not given the number.
 *
 * So screening records a verdict here, and the broadcast consumes it.
 */
object CallGuard {

    private const val TAG = "VoiceShieldCallGuard"

    /** Set by screening while the phone is still ringing. */
    @Volatile
    private var armedNumber: String? = null

    @Volatile
    private var armed: Boolean = false

    fun arm(number: String?) {
        armed = true
        armedNumber = number
        Log.i(TAG, "armed for ${redact(number)}")
    }

    fun disarm() {
        armed = false
        armedNumber = null
    }

    fun isArmed(): Boolean = armed

    fun armedFor(): String? = armedNumber

    /**
     * Whether a number should trigger the guard.
     *
     * Known contacts are trusted and never trigger it. This is what keeps VoiceShield
     * silent through the 99% of calls that are family, and it is the single most important
     * rule for the product being tolerable to live with: an app that reacts to every call
     * gets uninstalled in a week.
     *
     * Without READ_CONTACTS we cannot tell family from stranger, so we fail OPEN and guard
     * the call. A needless guard session is a wasted minute; a missed one can cost a
     * pension.
     */
    fun shouldGuard(context: Context, number: String?): Boolean {
        if (number.isNullOrBlank()) {
            // Withheld/private numbers are disproportionately scams.
            Log.i(TAG, "no caller id -> guard")
            return true
        }
        if (!hasContactsPermission(context)) {
            Log.i(TAG, "no contacts permission -> guard (fail open)")
            return true
        }
        val known = isKnownContact(context, number)
        Log.i(TAG, "${redact(number)} known=$known")
        return !known
    }

    private fun hasContactsPermission(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CONTACTS) ==
            PackageManager.PERMISSION_GRANTED

    private fun isKnownContact(context: Context, number: String): Boolean {
        val uri = Uri.withAppendedPath(
            ContactsContract.PhoneLookup.CONTENT_FILTER_URI,
            Uri.encode(number),
        )
        return try {
            context.contentResolver.query(
                uri,
                arrayOf(ContactsContract.PhoneLookup._ID),
                null,
                null,
                null,
            )?.use { it.moveToFirst() } ?: false
        } catch (e: SecurityException) {
            Log.w(TAG, "contact lookup denied", e)
            false
        } catch (e: Exception) {
            Log.w(TAG, "contact lookup failed", e)
            false
        }
    }

    /** Never log a full phone number. */
    private fun redact(number: String?): String {
        if (number.isNullOrBlank()) return "<withheld>"
        return if (number.length <= 4) "***" else "***" + number.takeLast(4)
    }
}

package com.voiceshield.guard.call

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import com.voiceshield.MainActivity
import com.voiceshield.guard.GuardService
import com.voiceshield.guard.Trigger

/**
 * The one-tap fallback path.
 *
 * When the dialer role is granted, the guard starts by itself and the elder never sees
 * this. When it is not, this heads-up notification appears while the phone is ringing with
 * a single oversized action, so the flow is one tap from the shade instead of four steps
 * through an app.
 */
object CallAlerts {

    private const val CHANNEL_ID = "voiceshield_incoming"
    private const val NOTIFICATION_ID = 43

    fun showArmedWhileRinging(context: Context, number: String?) {
        ensureChannel(context)

        val start = PendingIntent.getService(
            context,
            0,
            GuardService.startIntent(context, Trigger.UNKNOWN_CALLER),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val open = PendingIntent.getActivity(
            context,
            1,
            Intent(context, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val who = if (number.isNullOrBlank()) "Unknown number" else number
        val note = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_lock_idle_lock)
            .setContentTitle("अनजान नंबर — $who")
            .setContentText("Turn on speaker, then tap Protect this call")
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setAutoCancel(true)
            .setContentIntent(open)
            // Full-screen intent is best-effort: from Android 14 it is restricted to
            // calling and alarm apps, and degrades to a heads-up notification otherwise.
            .setFullScreenIntent(open, true)
            .addAction(
                android.R.drawable.ic_lock_idle_lock,
                "सुरक्षा चालू करें · Protect this call",
                start,
            )
            .build()

        NotificationManagerCompatSafe(context).notify(NOTIFICATION_ID, note)
    }

    fun dismissArmed(context: Context) {
        context.getSystemService(NotificationManager::class.java)?.cancel(NOTIFICATION_ID)
    }

    private fun ensureChannel(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                "Incoming unknown calls",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = "Offers protection when an unknown number calls"
                setShowBadge(false)
            }
        )
    }

    /** Posting can throw if POST_NOTIFICATIONS was denied; never crash a live call over it. */
    private class NotificationManagerCompatSafe(private val context: Context) {
        fun notify(id: Int, notification: android.app.Notification) {
            try {
                context.getSystemService(NotificationManager::class.java)
                    ?.notify(id, notification)
            } catch (_: SecurityException) {
                // Notifications denied — the manual in-app button still works.
            }
        }
    }
}

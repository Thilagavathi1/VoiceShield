package com.voiceshield.guard

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat

/**
 * Holds the microphone for the duration of a guarded call, and owns the session lifecycle.
 *
 * Android 14+ requires a `microphone`-typed foreground service for mic access once the app
 * is no longer visible, which is exactly our situation: the elder is in the dialer, not in
 * VoiceShield. The service must exist before we background, so it is started at the same
 * moment the guard starts rather than lazily.
 *
 * It drives GuardController rather than holding state itself, so a manual start from the UI
 * and an automatic start from an unknown-caller screening converge on the same path.
 */
class GuardService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFICATION_ID, buildNotification())

        when (intent?.action) {
            ACTION_STOP -> {
                GuardController.stop(this)
                stopSelf()
                return START_NOT_STICKY
            }

            else -> {
                val trigger = runCatching {
                    Trigger.valueOf(intent?.getStringExtra(EXTRA_TRIGGER) ?: Trigger.MANUAL.name)
                }.getOrDefault(Trigger.MANUAL)
                Log.i(TAG, "guard service starting session via $trigger")
                GuardController.start(this, trigger)
            }
        }
        // Do not resurrect with a null intent: a restarted guard with no live call would
        // hold the mic for nothing.
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        GuardController.stop(this)
        super.onDestroy()
    }

    private fun buildNotification(): Notification {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "VoiceShield protection",
                NotificationManager.IMPORTANCE_LOW,
            ).apply { description = "Shown while VoiceShield is listening for scam calls" }
            getSystemService(NotificationManager::class.java)
                .createNotificationChannel(channel)
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("VoiceShield இயங்குகிறது")
            .setContentText("VoiceShield is protecting this call")
            .setSmallIcon(android.R.drawable.ic_lock_idle_lock)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    companion object {
        private const val TAG = "VoiceShieldService"
        private const val CHANNEL_ID = "voiceshield_guard"
        private const val NOTIFICATION_ID = 42

        private const val ACTION_STOP = "com.voiceshield.action.STOP_GUARD"
        private const val EXTRA_TRIGGER = "trigger"

        fun startIntent(context: Context, trigger: Trigger): Intent =
            Intent(context, GuardService::class.java).putExtra(EXTRA_TRIGGER, trigger.name)

        fun start(context: Context, trigger: Trigger) {
            val intent = startIntent(context, trigger)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            if (!GuardController.isActive) return
            val intent = Intent(context, GuardService::class.java).setAction(ACTION_STOP)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }
}

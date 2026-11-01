package id.my.yhdev.nexo.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.RingtoneManager
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import id.my.yhdev.nexo.MainActivity
import id.my.yhdev.nexo.R

open class NexoFirebaseMessagingService : FirebaseMessagingService() {

    private val TAG = "NexoFCM"
    private val CHANNEL_ID = "nexo_notifications"

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        Log.i(TAG, "FCM device token refreshed")
        // Store locally so it can be synced with Nexo server
        val prefs = getSharedPreferences("nexo_fcm", Context.MODE_PRIVATE)
        prefs.edit().putString("fcm_token", token).apply()
        // Backward compatibility sync with jarvis_fcm
        val legacyPrefs = getSharedPreferences("jarvis_fcm", Context.MODE_PRIVATE)
        legacyPrefs.edit().putString("fcm_token", token).apply()
    }

    override fun onMessageReceived(remoteMessage: RemoteMessage) {
        super.onMessageReceived(remoteMessage)
        Log.i(TAG, "FCM Message received from: ${remoteMessage.from}")

        val title = remoteMessage.notification?.title
            ?: remoteMessage.data["title"]
            ?: "Nexo Assistant"

        val body = remoteMessage.notification?.body
            ?: remoteMessage.data["body"]
            ?: "Ada pesan atau instruksi baru dari sistem."

        val expression = remoteMessage.data["expression"] ?: "netral"

        showNotification(title, body, expression)
    }

    private fun showNotification(title: String, body: String, expression: String) {
        val intent = Intent(this, MainActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            putExtra("notification_title", title)
            putExtra("notification_body", body)
            putExtra("notification_expression", expression)
        }

        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            intent,
            PendingIntent.FLAG_ONE_SHOT or PendingIntent.FLAG_IMMUTABLE
        )

        val defaultSoundUri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)

        createNotificationChannel()

        val notificationBuilder = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_popup_reminder)
            .setContentTitle(title)
            .setContentText(body)
            .setAutoCancel(true)
            .setSound(defaultSoundUri)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(pendingIntent)

        val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        notificationManager.notify(System.currentTimeMillis().toInt(), notificationBuilder.build())
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Nexo Alerts & Events",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Notifikasi suara dan pesan dari Nexo Agent saat ponsel terkunci"
                enableVibration(true)
            }
            val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            notificationManager.createNotificationChannel(channel)
        }
    }
}

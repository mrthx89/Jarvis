package com.yhdev.jarvis

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.yhdev.jarvis.config.RemoteConfigManager
import com.yhdev.jarvis.service.JarvisKioskService
import com.yhdev.jarvis.ui.JarvisState
import com.yhdev.jarvis.ui.VisualizerView

class MainActivity : AppCompatActivity(), JarvisKioskService.ServiceCallback {

    private lateinit var visualizerView: VisualizerView
    private lateinit var tvStatus: TextView
    private lateinit var tvSubtitle: TextView
    private lateinit var remoteConfigManager: RemoteConfigManager

    private var kioskService: JarvisKioskService? = null
    private var isBound = false

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            val binder = service as JarvisKioskService.LocalBinder
            kioskService = binder.getService()
            kioskService?.serviceCallback = this@MainActivity
            isBound = true

            val deviceId = "jarvis_appliance_${Build.SERIAL.takeIf { it != "unknown" } ?: "tablet_01"}"
            
            // Dynamically resolve server URL via Firebase Remote Config with fallback
            remoteConfigManager.fetchServerUrl { serverUrl ->
                kioskService?.initializeConnection(serverUrl, deviceId)
            }

            // Listen for live URL updates from Firebase Console
            remoteConfigManager.listenForRealtimeUpdates { newUrl ->
                runOnUiThread {
                    tvStatus.text = "Mengalihkan ke server baru..."
                }
                kioskService?.initializeConnection(newUrl, deviceId)
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            kioskService = null
            isBound = false
        }
    }

    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { isGranted: Boolean ->
        if (isGranted) {
            bindKioskService()
        } else {
            tvStatus.text = "Izin mikrofon diperlukan untuk mendengarkan."
            visualizerView.setState(JarvisState.ERROR)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        remoteConfigManager = RemoteConfigManager(this)

        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        hideSystemUI()

        visualizerView = findViewById(R.id.visualizerView)
        tvStatus = findViewById(R.id.tvStatus)
        tvSubtitle = findViewById(R.id.tvSubtitle)

        checkAndRequestAudioPermission()
    }

    private fun checkAndRequestAudioPermission() {
        if (ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.RECORD_AUDIO
            ) == PackageManager.PERMISSION_GRANTED
        ) {
            bindKioskService()
        } else {
            requestPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun bindKioskService() {
        val serviceIntent = Intent(this, JarvisKioskService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(serviceIntent)
        } else {
            startService(serviceIntent)
        }
        bindService(serviceIntent, serviceConnection, Context.BIND_AUTO_CREATE)
    }

    override fun onResume() {
        super.onResume()
        hideSystemUI()
    }

    private fun hideSystemUI() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.insetsController?.let { controller ->
                controller.hide(WindowInsets.Type.statusBars() or WindowInsets.Type.navigationBars())
                controller.systemBarsBehavior =
                    WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = (
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                    or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                    or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                    or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    or View.SYSTEM_UI_FLAG_FULLSCREEN
            )
        }
    }

    override fun onStateChanged(state: JarvisState, message: String?, expression: String?) {
        runOnUiThread {
            visualizerView.setState(state, expression)
            tvStatus.text = when (state) {
                JarvisState.IDLE -> getString(R.string.status_idle)
                JarvisState.LISTENING -> getString(R.string.status_listening)
                JarvisState.THINKING -> getString(R.string.status_thinking)
                JarvisState.SPEAKING -> getString(R.string.status_speaking)
                JarvisState.ERROR -> message ?: "Terjadi kesalahan"
                JarvisState.DISCONNECTED -> getString(R.string.status_disconnected)
            }
        }
    }

    override fun onTelemetryReceived(amplitude: Float, bands: List<Float>?) {
        runOnUiThread {
            visualizerView.setAmplitude(amplitude)
        }
    }

    override fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean) {
        runOnUiThread {
            tvSubtitle.text = text
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        if (isBound) {
            unbindService(serviceConnection)
            isBound = false
        }
    }
}

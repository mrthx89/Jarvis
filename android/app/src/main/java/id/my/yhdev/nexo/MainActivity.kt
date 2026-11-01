package id.my.yhdev.nexo

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.View
import android.view.ViewGroup
import android.transition.ChangeBounds
import android.transition.TransitionManager
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.widget.TextView
import android.widget.Button
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import id.my.yhdev.nexo.config.RemoteConfigManager
import id.my.yhdev.nexo.security.DeviceCredentialStore
import id.my.yhdev.nexo.service.NexoKioskService
import id.my.yhdev.nexo.ui.CredentialSetupSheet
import id.my.yhdev.nexo.ui.NexoState
import id.my.yhdev.nexo.ui.VisualizerView

class MainActivity : AppCompatActivity(), NexoKioskService.ServiceCallback {

    private lateinit var visualizerView: VisualizerView
    private lateinit var tvStatus: TextView
    private lateinit var tvSubtitle: TextView
    private lateinit var toggleVoice: Button
    private var voiceEnabled = false
    private var voiceStateKnown = false
    private lateinit var voiceMood: TextView
    private lateinit var consoleCard: View
    private val uiHandler = Handler(Looper.getMainLooper())
    private var voiceMoodDeadline = 0L
    private var uiActive = false
    private val hideVoiceMood = Runnable { if (!isDestroyed) renderVoiceMood() }
    private lateinit var copyReply: androidx.appcompat.widget.AppCompatImageButton
    private var lastAssistantReply = ""
    private lateinit var ivStatusLed: android.widget.ImageView
    private lateinit var remoteConfigManager: RemoteConfigManager

    private var kioskService: NexoKioskService? = null
    private var isBound = false
    private var bindingRequested = false
    private var hasTranscription = false
    private val credentialStore by lazy { DeviceCredentialStore(this) }
    private var currentServerUrl: String? = null
    private var credentialSheet: CredentialSetupSheet? = null
    private val deviceId: String by lazy {
        val androidId = android.provider.Settings.Secure.getString(
            contentResolver, android.provider.Settings.Secure.ANDROID_ID)
        // Persist a random fallback; never share a hard-coded ID across devices.
        val identityPrefs = getSharedPreferences("device_identity", Context.MODE_PRIVATE)
        val legacyId = identityPrefs.getString("nexo_device_id", null)
            ?: identityPrefs.getString("id", null)
        val localId = androidId?.takeIf { it.isNotBlank() } ?: legacyId
            ?: java.util.UUID.randomUUID().toString().also { identityPrefs.edit().putString("id", it).apply() }
        identityPrefs.getString("device_id", null) ?: "nexo_appliance_$localId"
    }

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            val binder = service as NexoKioskService.LocalBinder
            kioskService = binder.getService()
            kioskService?.serviceCallback = this@MainActivity
            isBound = true
            bindingRequested = false

            // Dynamically resolve server URL via Firebase Remote Config with fallback
            remoteConfigManager.fetchServerUrl(onUnavailable = {
                runOnUiThread {
                    if (!isDestroyed && isBound) {
                        onStateChanged(NexoState.ERROR, "Endpoint WSS tidak tersedia. Periksa konfigurasi server.", null)
                    }
                }
            }) { serverUrl ->
                runOnUiThread {
                    currentServerUrl = serverUrl
                    if (!isDestroyed && isBound) kioskService?.initializeConnection(serverUrl, deviceId)
                }
            }

            // Listen for live URL updates from Firebase Console
            remoteConfigManager.listenForRealtimeUpdates { newUrl ->
                runOnUiThread {
                    currentServerUrl = newUrl
                    if (!isDestroyed && isBound) kioskService?.initializeConnection(newUrl, deviceId)
                }
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            kioskService = null
            isBound = false
            toggleVoice.isEnabled = false
            bindingRequested = true // Framework retains the binding and can reconnect it.
            onStateChanged(NexoState.DISCONNECTED, "Layanan suara terputus.", null)
        }
    }

    private val requestMultiplePermissionsLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions ->
        val audioGranted = permissions[Manifest.permission.RECORD_AUDIO] ?: (
            ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        )
        if (audioGranted) {
            bindKioskService()
        } else {
            onStateChanged(NexoState.ERROR, "Izin mikrofon diperlukan untuk mendengarkan.", null)
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
        tvStatus.setOnLongClickListener { showCredentialSetup(); true }
        tvStatus.setOnClickListener {
            Toast.makeText(this, tvStatus.contentDescription, Toast.LENGTH_LONG).show()
        }
        consoleCard = findViewById(R.id.consoleCard)
        voiceMood = findViewById(R.id.voiceMood)
        visualizerView.setVoiceEnabled(false, animateReaction = false)
        toggleVoice = findViewById(R.id.toggleVoice)
        toggleVoice.setOnClickListener {
            val service = kioskService ?: return@setOnClickListener
            if (service.isVoiceEnabled) service.stopVoice() else service.startVoice()
        }
        tvSubtitle = findViewById(R.id.tvSubtitle)
        tvSubtitle.movementMethod = android.text.method.ScrollingMovementMethod.getInstance()
        copyReply = findViewById(R.id.copyReply)
        copyReply.setOnClickListener {
            if (lastAssistantReply.isNotBlank()) {
                val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                clipboard.setPrimaryClip(
                    ClipData.newPlainText(getString(R.string.reply_clip_label), lastAssistantReply)
                )
                Toast.makeText(this, R.string.reply_copied, Toast.LENGTH_SHORT).show()
            }
        }
        ivStatusLed = findViewById(R.id.ivStatusLed)

        onStateChanged(NexoState.DISCONNECTED, "Memeriksa izin mikrofon...", null)
        checkAndRequestAudioPermission()
    }

    private fun showCredentialSetup() {
        if (credentialSheet != null || isFinishing || isDestroyed) return
        val serverUrl = currentServerUrl
        val origin = serverUrl?.let { credentialStore.origin(it) }
        if (serverUrl == null || origin == null) {
            onStateChanged(NexoState.ERROR, "Endpoint WSS belum tersedia. Coba lagi setelah konfigurasi dimuat.", null)
            return
        }
        credentialSheet = CredentialSetupSheet(
            activity = this,
            deviceId = deviceId,
            origin = origin,
            isCurrentOrigin = { currentServerUrl == serverUrl },
            onSave = { token ->
                credentialStore.save(serverUrl, deviceId, token)
                hasTranscription = false
                kioskService?.refreshCredentials()
            },
            onReset = {
                credentialStore.clear()
                hasTranscription = false
                kioskService?.refreshCredentials()
            },
            onDismiss = { credentialSheet = null }
        ).also { it.show() }
    }

    private fun checkAndRequestAudioPermission() {
        val permissionsToRequest = mutableListOf<String>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            permissionsToRequest.add(Manifest.permission.RECORD_AUDIO)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(
                    this,
                    Manifest.permission.POST_NOTIFICATIONS
                ) != PackageManager.PERMISSION_GRANTED
            ) {
                permissionsToRequest.add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }

        if (permissionsToRequest.isEmpty()) {
            bindKioskService()
        } else {
            requestMultiplePermissionsLauncher.launch(permissionsToRequest.toTypedArray())
        }
    }

    private fun bindKioskService() {
        if (isBound || bindingRequested || isDestroyed) return
        bindingRequested = true
        onStateChanged(NexoState.DISCONNECTED, "Memulai layanan suara...", null)
        val serviceIntent = Intent(this, NexoKioskService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(serviceIntent)
        } else {
            startService(serviceIntent)
        }
        if (!bindService(serviceIntent, serviceConnection, Context.BIND_AUTO_CREATE)) {
            bindingRequested = false
            onStateChanged(NexoState.ERROR, "Layanan suara gagal dihubungkan.", null)
        }
    }

    override fun onResume() {
        super.onResume()
        uiActive = true
        renderVoiceMood()
        hideSystemUI()
    }

    override fun onStop() {
        uiActive = false
        uiHandler.removeCallbacks(hideVoiceMood)
        super.onStop()
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

    private var lastState = NexoState.DISCONNECTED
    private var lastMessage: String? = null
    private var lastExpression: String? = null

    override fun onStateChanged(state: NexoState, message: String?, expression: String?) {
        runOnUiThread {
            lastState = state
            lastMessage = message
            lastExpression = expression
            renderStatus()
        }
    }

    override fun onVoiceEnabledChanged(enabled: Boolean) {
        runOnUiThread {
            if (isDestroyed) return@runOnUiThread
            val firstSync = !voiceStateKnown
            val changed = voiceStateKnown && voiceEnabled != enabled
            voiceEnabled = enabled
            voiceStateKnown = true
            visualizerView.setVoiceEnabled(enabled, animateReaction = changed)
            if (firstSync || changed) {
                voiceMoodDeadline = if (enabled) SystemClock.elapsedRealtime() + 15_000L else 0L
                voiceMood.setText(if (enabled) R.string.voice_mood_on else R.string.voice_mood_off)
            }
            renderVoiceMood()
            // OFF hides the whole readout, not the stored last final answer.
            consoleCard.visibility = if (enabled) View.VISIBLE else View.GONE
            toggleVoice.isEnabled = true
            toggleVoice.setText(if (enabled) R.string.voice_stop else R.string.voice_start)
            renderStatus()
        }
    }

    private fun renderVoiceMood() {
        uiHandler.removeCallbacks(hideVoiceMood)
        val remaining = voiceMoodDeadline - SystemClock.elapsedRealtime()
        voiceMood.visibility = if (voiceEnabled && remaining <= 0L) View.GONE else View.VISIBLE
        if (voiceEnabled && remaining > 0L && uiActive) {
            uiHandler.postDelayed(hideVoiceMood, remaining)
        }
    }

    private fun renderStatus() {
        val state = lastState
        val message = lastMessage
        visualizerView.setState(if (voiceEnabled) state else NexoState.IDLE, lastExpression)
        val status = getString(when {
            state == NexoState.ERROR -> R.string.voice_error
            !voiceEnabled -> R.string.voice_paused
            else -> when (state) {
                NexoState.IDLE, NexoState.LISTENING -> R.string.voice_listening
                NexoState.THINKING -> R.string.voice_thinking
                NexoState.SPEAKING -> R.string.voice_speaking
                NexoState.ERROR -> R.string.voice_error
                NexoState.DISCONNECTED -> R.string.voice_connecting
            }
        })
        if (tvStatus.text.toString() != status) {
            TransitionManager.beginDelayedTransition(
                findViewById<ViewGroup>(R.id.mainRoot),
                ChangeBounds().apply { duration = 180; addTarget(R.id.pillStatus) }
            )
            tvStatus.text = status
        }
        tvStatus.contentDescription = message ?: status
        // Actionable errors remain visible; copy still retains the full last reply.
        if (!hasTranscription || state == NexoState.ERROR) {
            tvSubtitle.text = message?.takeIf { it.isNotBlank() } ?: status
        }
        val ledColor = when {
            state == NexoState.ERROR -> 0xFFFF5252.toInt()
            !voiceEnabled -> 0xFF87909C.toInt()
            else -> when (state) {
                NexoState.IDLE -> 0xFF00E5FF.toInt()
                NexoState.LISTENING -> 0xFF00E676.toInt()
                NexoState.THINKING -> 0xFFFFB300.toInt()
                NexoState.SPEAKING -> 0xFF2979FF.toInt()
                NexoState.ERROR, NexoState.DISCONNECTED -> 0xFFFF5252.toInt()
            }
        }
        ivStatusLed.setColorFilter(ledColor)
    }

    override fun onTelemetryReceived(amplitude: Float, bands: List<Float>?) {
        runOnUiThread {
            visualizerView.setAmplitude(amplitude)
        }
    }

    override fun onTranscriptionReceived(role: String, text: String, isFinal: Boolean) {
        runOnUiThread {
            hasTranscription = true
            tvSubtitle.text = text
            tvSubtitle.scrollTo(0, 0)
            // Keep the last complete answer even while the next user utterance
            // or a status is displayed. Nothing is copied automatically.
            if (role == "assistant" && isFinal) {
                lastAssistantReply = text
                copyReply.isEnabled = text.isNotBlank()
                copyReply.alpha = if (copyReply.isEnabled) 1f else 0.35f
            }
        }
    }

    override fun onDestroy() {
        uiHandler.removeCallbacks(hideVoiceMood)
        super.onDestroy()
        credentialSheet?.dismiss()
        credentialSheet = null
        remoteConfigManager.close()
        kioskService?.serviceCallback = null
        kioskService = null
        if (isBound || bindingRequested) {
            unbindService(serviceConnection)
            isBound = false
            bindingRequested = false
        }
    }
}

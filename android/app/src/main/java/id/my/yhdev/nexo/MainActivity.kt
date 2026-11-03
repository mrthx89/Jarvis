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
import android.view.MotionEvent
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.widget.TextView
import android.widget.Button
import android.widget.Toast
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
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
    private lateinit var pillStatus: View
    private lateinit var pillVoiceprint: View
    private lateinit var ivVoiceprintLed: android.widget.ImageView
    private lateinit var tvVoiceprintStatus: TextView
    private var voiceprintSheet: id.my.yhdev.nexo.ui.VoiceprintManagementSheet? = null
    private var lastVoiceprintDialog: id.my.yhdev.nexo.network.VoiceprintDialog? = null
    private lateinit var remoteConfigManager: RemoteConfigManager

    // HUD Pixel-Shifting for OLED/IPS Burn-In Protection (2-3px every 3-5 minutes)
    private val hudShiftOffsets = arrayOf(
        0f to 0f, 2f to 0f, 2f to 2f, 0f to 2f, -2f to 1f, -1f to -2f, 1f to -2f, 2f to -1f
    )
    private var hudShiftIndex = 0
    private val pixelShiftRunnable = object : Runnable {
        override fun run() {
            if (isDestroyed || !uiActive) return
            hudShiftIndex = (hudShiftIndex + 1) % hudShiftOffsets.size
            val (dx, dy) = hudShiftOffsets[hudShiftIndex]
            if (::pillStatus.isInitialized) {
                pillStatus.animate().translationX(dx).translationY(dy).setDuration(2200).start()
            }
            if (::pillVoiceprint.isInitialized) {
                pillVoiceprint.animate().translationX(dx).translationY(dy).setDuration(2200).start()
            }
            if (::toggleVoice.isInitialized) {
                toggleVoice.animate().translationX(dx).translationY(dy).setDuration(2200).start()
            }
            if (::voiceMood.isInitialized) {
                voiceMood.animate().translationX(dx).translationY(dy).setDuration(2200).start()
            }
            if (::consoleCard.isInitialized) {
                consoleCard.animate().translationX(dx).translationY(dy).setDuration(2200).start()
            }
            uiHandler.postDelayed(this, 210_000L) // 3.5 minutes
        }
    }

    // Inactivity & Ambient Sleep Dimming (> 5 minutes idle)
    private var lastInteractionTime = SystemClock.uptimeMillis()
    private val ambientCheckRunnable = object : Runnable {
        override fun run() {
            if (isDestroyed || !uiActive) return
            val idleDuration = SystemClock.uptimeMillis() - lastInteractionTime
            val isIdleQuiet = lastState == NexoState.IDLE || lastState == NexoState.DISCONNECTED
            val targetAlpha = if (isIdleQuiet && idleDuration > 300_000L) 0.42f else 1.0f
            if (::pillStatus.isInitialized && pillStatus.alpha != targetAlpha) {
                pillStatus.animate().alpha(targetAlpha).setDuration(1200).start()
            }
            if (::pillVoiceprint.isInitialized && pillVoiceprint.alpha != targetAlpha) {
                pillVoiceprint.animate().alpha(targetAlpha).setDuration(1200).start()
            }
            uiHandler.postDelayed(this, 30_000L)
        }
    }

    private var isSleepingMode = false

    private fun setHardwareBrightness(dimmed: Boolean) {
        val lp = window.attributes
        lp.screenBrightness = if (dimmed) 0.03f else WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE
        window.attributes = lp
    }

    private fun applySleepModeUi(sleeping: Boolean) {
        if (isSleepingMode == sleeping) return
        isSleepingMode = sleeping
        setHardwareBrightness(sleeping)

        if (sleeping) {
            if (::pillStatus.isInitialized) {
                pillStatus.animate().alpha(0f).setDuration(450).withEndAction {
                    if (isSleepingMode) pillStatus.visibility = View.GONE
                }
            }
            if (::pillVoiceprint.isInitialized) {
                pillVoiceprint.animate().alpha(0f).setDuration(450).withEndAction {
                    if (isSleepingMode) pillVoiceprint.visibility = View.GONE
                }
            }
            if (::consoleCard.isInitialized) {
                consoleCard.animate().alpha(0f).setDuration(450).withEndAction {
                    if (isSleepingMode) consoleCard.visibility = View.GONE
                }
            }
        } else {
            if (::pillStatus.isInitialized) {
                pillStatus.visibility = View.VISIBLE
                pillStatus.animate().alpha(1f).setDuration(350).start()
            }
            if (::pillVoiceprint.isInitialized) {
                pillVoiceprint.visibility = View.VISIBLE
                pillVoiceprint.animate().alpha(1f).setDuration(350).start()
            }
            if (::consoleCard.isInitialized && voiceEnabled) {
                consoleCard.visibility = View.VISIBLE
                consoleCard.animate().alpha(1f).setDuration(350).start()
            }
        }
    }

    override fun dispatchTouchEvent(ev: MotionEvent?): Boolean {
        if (isSleepingMode && ev?.action == MotionEvent.ACTION_DOWN) {
            resetActivity()
            visualizerView.triggerWakeSequence()
            return true
        }
        return super.dispatchTouchEvent(ev)
    }

    private fun resetActivity() {
        lastInteractionTime = SystemClock.uptimeMillis()
        if (isSleepingMode) {
            applySleepModeUi(false)
        }
        if (::pillStatus.isInitialized && pillStatus.alpha < 0.95f) {
            pillStatus.animate().alpha(1.0f).setDuration(400).start()
        }
        if (::pillVoiceprint.isInitialized && pillVoiceprint.alpha < 0.95f) {
            pillVoiceprint.animate().alpha(1.0f).setDuration(400).start()
        }
    }

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
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            window.attributes.layoutInDisplayCutoutMode =
                WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }

        val mainRoot = findViewById<View>(R.id.mainRoot)
        ViewCompat.setOnApplyWindowInsetsListener(mainRoot) { view, windowInsets ->
            val insets = windowInsets.getInsets(
                WindowInsetsCompat.Type.systemBars() or
                WindowInsetsCompat.Type.displayCutout()
            )
            view.setPadding(insets.left, insets.top, insets.right, insets.bottom)
            windowInsets
        }

        remoteConfigManager = RemoteConfigManager(this)

        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        hideSystemUI()

        hudTopBar = findViewById(R.id.hudTopBar)
        pillStatus = findViewById(R.id.pillStatus)
        visualizerView = findViewById(R.id.visualizerView)
        tvStatus = findViewById(R.id.tvStatus)
        tvStatus.setOnLongClickListener { showCredentialSetup(); true }
        tvStatus.setOnClickListener {
            Toast.makeText(this, tvStatus.contentDescription, Toast.LENGTH_LONG).show()
        }
        pillVoiceprint = findViewById(R.id.pillVoiceprint)
        ivVoiceprintLed = findViewById(R.id.ivVoiceprintLed)
        tvVoiceprintStatus = findViewById(R.id.tvVoiceprintStatus)
        pillVoiceprint.setOnClickListener {
            resetActivity()
            showVoiceprintManagement()
        }
        consoleCard = findViewById(R.id.consoleCard)
        voiceMood = findViewById(R.id.voiceMood)
        visualizerView.setVoiceEnabled(false, animateReaction = false)
        toggleVoice = findViewById(R.id.toggleVoice)
        toggleVoice.setOnClickListener {
            resetActivity()
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

    private fun showVoiceprintManagement() {
        if (voiceprintSheet != null || isFinishing || isDestroyed) return
        voiceprintSheet = id.my.yhdev.nexo.ui.VoiceprintManagementSheet(
            activity = this,
            currentDialog = lastVoiceprintDialog,
            onUnenroll = {
                kioskService?.unenrollVoiceprint()
            },
            onDismiss = { voiceprintSheet = null }
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
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(serviceIntent)
            } else {
                startService(serviceIntent)
            }
            if (!bindService(serviceIntent, serviceConnection, Context.BIND_AUTO_CREATE)) {
                bindingRequested = false
                onStateChanged(NexoState.ERROR, "Layanan suara gagal dihubungkan.", null)
            }
        } catch (e: SecurityException) {
            bindingRequested = false
            onStateChanged(NexoState.ERROR, "Izin mikrofon diperlukan • buka ulang aplikasi untuk melanjutkan.", null)
        } catch (e: IllegalStateException) {
            bindingRequested = false
            onStateChanged(NexoState.ERROR, "Android membatasi layanan latar belakang • buka ulang aplikasi.", null)
        }
    }

    override fun onResume() {
        super.onResume()
        uiActive = true
        renderVoiceMood()
        uiHandler.removeCallbacks(pixelShiftRunnable)
        uiHandler.postDelayed(pixelShiftRunnable, 210_000L)
        uiHandler.removeCallbacks(ambientCheckRunnable)
        uiHandler.postDelayed(ambientCheckRunnable, 30_000L)
        hideSystemUI()
    }

    override fun onStop() {
        uiActive = false
        uiHandler.removeCallbacks(hideVoiceMood)
        uiHandler.removeCallbacks(pixelShiftRunnable)
        uiHandler.removeCallbacks(ambientCheckRunnable)
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
            resetActivity()
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
        val isSleeping = (lastExpression == "sleeping" || lastExpression == "tidur") &&
            (state == NexoState.IDLE || state == NexoState.LISTENING)
        applySleepModeUi(isSleeping)
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
        // Actionable errors and disconnect states remain visible; copy still retains the full last reply.
        if (!hasTranscription || state == NexoState.ERROR || state == NexoState.DISCONNECTED) {
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
                NexoState.DISCONNECTED -> 0xFFFFB300.toInt()
                NexoState.ERROR -> 0xFFFF5252.toInt()
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
            resetActivity()
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

    override fun onVoiceprintDialogReceived(dialog: id.my.yhdev.nexo.network.VoiceprintDialog) {
        runOnUiThread {
            if (isDestroyed) return@runOnUiThread
            lastVoiceprintDialog = dialog
            voiceprintSheet?.updateDialog(dialog)
            if (::tvVoiceprintStatus.isInitialized && ::ivVoiceprintLed.isInitialized) {
                val isEnrolled = dialog.isEnrolled
                tvVoiceprintStatus.setText(
                    if (isEnrolled) R.string.voiceprint_hud_registered
                    else R.string.voiceprint_hud_unregistered
                )
                val ledColor = if (isEnrolled) 0xFF00E676.toInt() else 0xFFFFB300.toInt()
                ivVoiceprintLed.setColorFilter(ledColor)
            }
        }
    }

    override fun onDestroy() {
        uiHandler.removeCallbacks(hideVoiceMood)
        uiHandler.removeCallbacks(pixelShiftRunnable)
        uiHandler.removeCallbacks(ambientCheckRunnable)
        super.onDestroy()
        voiceprintSheet?.dismiss()
        voiceprintSheet = null
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

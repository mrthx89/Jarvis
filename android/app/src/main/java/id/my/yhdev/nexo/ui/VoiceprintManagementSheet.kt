package id.my.yhdev.nexo.ui

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.LayoutInflater
import android.view.View
import android.widget.ImageView
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.button.MaterialButton
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import id.my.yhdev.nexo.R
import id.my.yhdev.nexo.network.VoiceprintDialog

class VoiceprintManagementSheet(
    private val activity: AppCompatActivity,
    private var currentDialog: VoiceprintDialog?,
    private val onEnroll: () -> Boolean,
    private val onFinish: () -> Boolean,
    private val onCancel: () -> Unit,
    private val onResumeMicrophone: () -> Unit,
    private val onUnenroll: () -> Unit,
    private val onDismiss: () -> Unit
) {
    private enum class Phase { READY, STARTING, RECORDING, SAVING, CANCELLING, DELETING }
    private val dialog = BottomSheetDialog(activity, R.style.ThemeOverlay_Nexo_CredentialSheet)
    private val handler = Handler(Looper.getMainLooper())
    private var phase = Phase.READY
    private var startedAt = 0L
    private var captureEnded = false
    private var retainResult = false
    private lateinit var close: MaterialButton
    private var queryTimeout: Runnable? = null
    private val operationTimeout = Runnable {
        if (dialog.isShowing && phase == Phase.DELETING) {
            phase = Phase.READY
            currentDialog = null
            feedback.setText(R.string.voiceprint_failed)
            renderProfile()
            renderControls()
        }
    }
    private var confirmation: AlertDialog? = null
    private lateinit var content: View
    private lateinit var feedback: TextView
    private lateinit var hint: TextView
    private lateinit var progress: ProgressBar
    private lateinit var inputLevel: ProgressBar
    private lateinit var enroll: MaterialButton
    private lateinit var cancel: MaterialButton
    private lateinit var remove: MaterialButton
    private val tick = object : Runnable {
        override fun run() {
            if (!dialog.isShowing || phase != Phase.RECORDING) return
            val elapsed = SystemClock.elapsedRealtime() - startedAt
            progress.progress = (elapsed * 100 / 4000L).toInt().coerceIn(0, 100)
            feedback.text = activity.getString(R.string.voiceprint_recording, (elapsed / 1000L + 1).toInt().coerceAtMost(4))
            if (elapsed >= 4000L) {
                phase = Phase.SAVING
                feedback.setText(R.string.voiceprint_saving)
                renderControls()
                if (!onFinish()) cancelWithError()
            } else handler.postDelayed(this, 100L)
        }
    }

    fun show() {
        content = LayoutInflater.from(dialog.context).inflate(R.layout.sheet_voiceprint_management, null)
        feedback = content.findViewById(R.id.voiceprintFeedback)
        hint = content.findViewById(R.id.tvEnrollHint)
        progress = content.findViewById(R.id.voiceprintProgress)
        inputLevel = content.findViewById(R.id.voiceprintInputLevel)
        enroll = content.findViewById(R.id.btnVoiceprintEnroll)
        cancel = content.findViewById(R.id.btnVoiceprintCancel)
        remove = content.findViewById(R.id.btnVoiceprintUnenroll)
        enroll.setOnClickListener {
            retainResult = false
            captureEnded = false
            phase = Phase.STARTING
            feedback.setText(R.string.voiceprint_wait)
            renderControls()
            if (!onEnroll()) {
                phase = Phase.READY
                feedback.setText(R.string.voiceprint_cannot_start)
                renderControls()
            }
        }
        cancel.setOnClickListener { cancelCapture() }
        remove.setOnClickListener { showDeleteConfirmation() }
        close = content.findViewById(R.id.btnVoiceprintClose)
        close.setOnClickListener {
            val resume = captureEnded && phase == Phase.READY
            dismiss()
            if (resume) onResumeMicrophone()
        }
        ViewCompat.setOnApplyWindowInsetsListener(content) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }
        dialog.setContentView(content)
        dialog.setOnShowListener {
            dialog.findViewById<View>(com.google.android.material.R.id.design_bottom_sheet)
                ?.setBackgroundResource(R.drawable.bg_bottom_sheet_modal)
            dialog.behavior.maxWidth = (560 * activity.resources.displayMetrics.density).toInt()
            dialog.behavior.skipCollapsed = true
            dialog.behavior.state = BottomSheetBehavior.STATE_EXPANDED
            ViewCompat.requestApplyInsets(content)
        }
        dialog.setOnDismissListener {
            handler.removeCallbacksAndMessages(null)
            confirmation?.dismiss()
            if (phase != Phase.READY && phase != Phase.DELETING) onCancel()
            onDismiss()
        }
        renderProfile()
        feedback.setText(when {
            currentDialog == null -> R.string.voiceprint_unknown
            currentDialog?.available != true -> R.string.voiceprint_unavailable
            else -> R.string.voiceprint_ready
        })
        renderControls()
        dialog.show()
        if (currentDialog == null) {
            queryTimeout = Runnable {
                if (dialog.isShowing && currentDialog == null && phase == Phase.READY) {
                    feedback.setText(R.string.voiceprint_unknown)
                }
            }.also { handler.postDelayed(it, 10_000L) }
        }
    }

    fun updateDialog(voiceDialog: VoiceprintDialog) {
        if (!::content.isInitialized) return
        currentDialog = voiceDialog.takeIf { it.available != null }
        queryTimeout?.let { handler.removeCallbacks(it) }
        if (voiceDialog.action != "query") handler.removeCallbacks(operationTimeout)
        renderProfile()
        when (voiceDialog.action) {
            "recording" -> {
                if (phase == Phase.STARTING) {
                    phase = Phase.RECORDING
                    startedAt = SystemClock.elapsedRealtime()
                    hint.setText(R.string.voiceprint_recording_phrase)
                    handler.post(tick)
                }
            }
            "enrolled", "unenroll" -> {
                captureEnded = phase != Phase.DELETING
                retainResult = true
                phase = Phase.READY
                handler.removeCallbacks(tick)
                feedback.text = if (voiceDialog.success == true) {
                    voiceDialog.message ?: activity.getString(R.string.voiceprint_saved)
                } else voiceDialog.message ?: activity.getString(R.string.voiceprint_failed)
            }
            "cancelled", "error" -> {
                captureEnded = phase != Phase.READY && phase != Phase.DELETING
                retainResult = true
                phase = Phase.READY
                handler.removeCallbacks(tick)
                feedback.text = voiceDialog.message ?: activity.getString(
                    if (voiceDialog.action == "cancelled") R.string.voiceprint_cancelled else R.string.voiceprint_failed
                )
            }
            "query" -> if (phase == Phase.READY && !retainResult) {
                feedback.text = if (voiceDialog.available == true) activity.getString(R.string.voiceprint_ready)
                    else voiceDialog.message ?: activity.getString(R.string.voiceprint_unavailable)
            }
        }
        renderControls()
    }

    fun updateInputLevel(amplitude: Float) {
        if (::inputLevel.isInitialized) inputLevel.progress =
            if (phase == Phase.RECORDING) (amplitude * 100).toInt().coerceIn(0, 100) else 0
    }

    private fun renderProfile() {
        val known = currentDialog != null
        val enrolled = currentDialog?.isEnrolled == true
        content.findViewById<TextView>(R.id.tvVoiceprintCount).setText(when {
            !known -> R.string.voiceprint_loading
            enrolled -> R.string.voiceprint_count_enrolled
            else -> R.string.voiceprint_count_open
        })
        content.findViewById<TextView>(R.id.tvVoiceprintDetails).setText(when {
            !known -> R.string.voiceprint_unknown
            enrolled -> R.string.voiceprint_info_enrolled
            else -> R.string.voiceprint_info_open
        })
        content.findViewById<ImageView>(R.id.ivSheetStatusLed).setColorFilter(
            if (!known) 0xFFB0B7C2.toInt() else if (enrolled) 0xFF00E676.toInt() else 0xFFFFB300.toInt()
        )
        val meta = currentDialog?.metadata
        content.findViewById<View>(R.id.layoutVoiceprintMeta).visibility =
            if (enrolled && meta != null) View.VISIBLE else View.GONE
        if (meta != null) content.findViewById<TextView>(R.id.tvVoiceprintMetaText).text = activity.getString(
            R.string.voiceprint_meta, meta.speaker.orEmpty(), meta.durationSeconds?.toString().orEmpty(), meta.enrolledAt.orEmpty()
        )
    }

    private fun renderControls() {
        val idle = phase == Phase.READY
        enroll.isEnabled = idle && !captureEnded && currentDialog?.available == true
        close.setText(if (idle && captureEnded) R.string.voiceprint_resume else R.string.voiceprint_btn_close)
        enroll.setText(if (currentDialog?.isEnrolled == true) R.string.voiceprint_btn_re_enroll else R.string.voiceprint_start)
        remove.visibility = if (currentDialog?.isEnrolled == true) View.VISIBLE else View.GONE
        remove.isEnabled = idle
        cancel.visibility = if (!idle && phase != Phase.DELETING) View.VISIBLE else View.GONE
        cancel.isEnabled = phase != Phase.CANCELLING
        progress.visibility = if (phase == Phase.RECORDING || phase == Phase.SAVING) View.VISIBLE else View.GONE
        if (phase != Phase.RECORDING) {
            inputLevel.progress = 0
            hint.setText(R.string.voiceprint_before_recording)
        }
    }

    private fun cancelCapture() {
        handler.removeCallbacks(tick)
        phase = Phase.CANCELLING
        feedback.setText(R.string.voiceprint_cancel_wait)
        renderControls()
        onCancel()
    }

    private fun cancelWithError() {
        cancelCapture()
        feedback.setText(R.string.voiceprint_failed)
    }

    private fun showDeleteConfirmation() {
        if (confirmation?.isShowing == true) return
        confirmation = MaterialAlertDialogBuilder(dialog.context)
            .setTitle(R.string.voiceprint_unenroll_confirm_title)
            .setMessage(R.string.voiceprint_unenroll_confirm_body)
            .setNegativeButton(R.string.voiceprint_unenroll_cancel, null)
            .setPositiveButton(R.string.voiceprint_unenroll_confirm_action) { _, _ ->
                phase = Phase.DELETING
                retainResult = false
                handler.postDelayed(operationTimeout, 10_000L)
                feedback.setText(R.string.voiceprint_deleting)
                renderControls()
                onUnenroll()
            }.show()
    }

    fun dismiss() {
        dialog.dismiss()
    }
}

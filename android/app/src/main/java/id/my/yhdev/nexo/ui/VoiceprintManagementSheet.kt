package id.my.yhdev.nexo.ui

import android.view.LayoutInflater
import android.view.View
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
import id.my.yhdev.nexo.service.NexoKioskService.RecordingSession

/** A view of service state, never a timer pretending that hardware/server is ready. */
class VoiceprintManagementSheet(
    private val activity: AppCompatActivity,
    private var currentDialog: VoiceprintDialog?,
    private val onEnroll: () -> Boolean,
    private val onFinish: () -> Boolean,
    private val onSave: () -> Boolean,
    private val onCancel: () -> Unit,
    private val onResumeMicrophone: () -> Unit,
    private val onUnenroll: () -> Unit,
    private val onDiagnostic: () -> Unit,
    private val onPlayDiagnostic: () -> Boolean,
    private val onDeleteDiagnostic: () -> Unit,
    private val onDismiss: () -> Unit
) {
    private val dialog = BottomSheetDialog(activity, R.style.ThemeOverlay_Nexo_CredentialSheet)
    private var confirmation: AlertDialog? = null
    private lateinit var content: View
    private lateinit var feedback: TextView
    private lateinit var level: ProgressBar
    private lateinit var enroll: MaterialButton
    private lateinit var finish: MaterialButton
    private lateinit var save: MaterialButton
    private lateinit var resume: MaterialButton
    private lateinit var cancel: MaterialButton
    private lateinit var remove: MaterialButton
    private lateinit var diagnostic: MaterialButton
    private lateinit var play: MaterialButton
    private lateinit var deleteClip: MaterialButton
    private var session = RecordingSession("ACQUIRING", "Menjeda percakapan…", false, 0, 0, false)
    fun show() {
        content = LayoutInflater.from(dialog.context).inflate(R.layout.sheet_voiceprint_management, null)
        feedback = content.findViewById(R.id.voiceprintFeedback)
        level = content.findViewById(R.id.voiceprintInputLevel)
        enroll = content.findViewById(R.id.btnVoiceprintEnroll)
        finish = content.findViewById(R.id.btnRecordingFinish)
        save = content.findViewById(R.id.btnRecordingSave)
        resume = content.findViewById(R.id.btnRecordingResume)
        cancel = content.findViewById(R.id.btnVoiceprintCancel)
        remove = content.findViewById(R.id.btnVoiceprintUnenroll)
        diagnostic = content.findViewById(R.id.btnDiagnosticOpen)
        play = content.findViewById(R.id.btnDiagnosticPlay)
        deleteClip = content.findViewById(R.id.btnDiagnosticDelete)
        enroll.setOnClickListener { if (!onEnroll()) feedback.setText(R.string.voiceprint_cannot_start) }
        finish.setOnClickListener { if (!onFinish()) feedback.setText(R.string.recording_finish_failed) }
        save.setOnClickListener { if (!onSave()) feedback.setText(R.string.recording_samples_missing) }
        cancel.setOnClickListener { onCancel() }
        remove.setOnClickListener { showDeleteConfirmation() }
        resume.setOnClickListener { onResumeMicrophone(); dismiss() }
        diagnostic.setOnClickListener {
            confirmation = MaterialAlertDialogBuilder(dialog.context)
                .setTitle(R.string.diagnostic_title).setMessage(R.string.diagnostic_consent)
                .setNegativeButton(R.string.voiceprint_unenroll_cancel, null)
                .setPositiveButton(R.string.diagnostic_agree) { _, _ -> onDiagnostic() }.show()
        }
        play.setOnClickListener { if (!onPlayDiagnostic()) feedback.setText(R.string.diagnostic_unavailable) }
        deleteClip.setOnClickListener { onDeleteDiagnostic() }
        content.findViewById<MaterialButton>(R.id.btnVoiceprintClose).setOnClickListener { dismiss() }
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
        dialog.setOnDismissListener { confirmation?.dismiss(); onDismiss() }
        renderProfile()
        renderControls()
        dialog.show()
    }
    fun onSessionChanged(value: RecordingSession) {
        session = value
        if (!::content.isInitialized) return
        feedback.text = value.message
        renderControls()
    }
    fun updateDialog(value: VoiceprintDialog) {
        currentDialog = value.takeIf { it.available != null }
        if (!::content.isInitialized) return
        renderProfile(); renderControls()
    }
    fun updateInputLevel(amplitude: Float) {
        if (::level.isInitialized) level.progress = if (session.state == "RECORDING")
            (amplitude * 100).toInt().coerceIn(0, 100) else 0
    }
    private fun renderControls() {
        val state = session.state
        val ready = session.ready
        val allSamplesReady = !session.diagnostic && session.requiredSamples > 0 && session.sampleCount > session.requiredSamples
        val recording = state == "RECORDING"
        val inEnrollmentFlow = session.requiredSamples > 0 && state !in setOf("SAVED", "CLOSED", "ERROR")
        enroll.isEnabled = ready && (session.diagnostic || currentDialog?.available == true) &&
            (session.requiredSamples == 0 || session.sampleCount <= session.requiredSamples)
        enroll.setText(when {
            session.diagnostic -> R.string.diagnostic_record
            session.requiredSamples > 0 -> R.string.voiceprint_start
            currentDialog?.isEnrolled == true -> R.string.voiceprint_btn_re_enroll
            else -> R.string.voiceprint_btn_enroll
        })
        enroll.visibility = if (recording || allSamplesReady || state in setOf("SAVED", "SAVING") || (session.diagnostic && state == "LOCAL_READY")) View.GONE else View.VISIBLE
        finish.visibility = if (recording) View.VISIBLE else View.GONE
        finish.isEnabled = state == "RECORDING"
        save.visibility = if (allSamplesReady || state == "SAVING") View.VISIBLE else View.GONE
        save.isEnabled = ready && state == "SAMPLES_READY"
        resume.visibility = if (state == "SAVED") View.VISIBLE else View.GONE
        resume.isEnabled = ready || state == "SAVED"
        remove.visibility = if (!session.diagnostic && currentDialog?.isEnrolled == true && !recording && !inEnrollmentFlow && state != "SAVED") View.VISIBLE else View.GONE
        remove.isEnabled = ready && state == "READY"
        diagnostic.visibility = View.GONE
        diagnostic.isEnabled = ready
        play.visibility = if (session.diagnostic && state == "LOCAL_READY") View.VISIBLE else View.GONE
        deleteClip.visibility = play.visibility
        play.isEnabled = ready; deleteClip.isEnabled = ready
        cancel.visibility = if (recording || inEnrollmentFlow) View.VISIBLE else View.GONE
        cancel.isEnabled = state != "RELEASING"
        content.findViewById<View>(R.id.voiceprintProgress).visibility = View.GONE
        content.findViewById<TextView>(R.id.tvEnrollHint).setText(if (session.diagnostic) R.string.diagnostic_hint else R.string.recording_samples_hint)
        if (state != "RECORDING") level.progress = 0
    }
    private fun renderProfile() {
        val known = currentDialog?.available != null
        content.findViewById<TextView>(R.id.tvVoiceprintCount).setText(when {
            !known -> R.string.voiceprint_loading
            currentDialog?.isEnrolled == true -> R.string.voiceprint_count_enrolled
            else -> R.string.voiceprint_count_open
        })
        content.findViewById<TextView>(R.id.tvVoiceprintDetails).setText(when {
            !known -> R.string.voiceprint_unknown
            currentDialog?.isEnrolled == true -> R.string.voiceprint_info_enrolled
            else -> R.string.voiceprint_info_open
        })
        content.findViewById<View>(R.id.layoutVoiceprintMeta).visibility = View.GONE
        content.findViewById<View>(R.id.ivSheetStatusLed).visibility = View.GONE
    }
    private fun showDeleteConfirmation() {
        if (confirmation?.isShowing == true) return
        confirmation = MaterialAlertDialogBuilder(dialog.context)
            .setTitle(R.string.voiceprint_unenroll_confirm_title)
            .setMessage(R.string.voiceprint_unenroll_confirm_body)
            .setNegativeButton(R.string.voiceprint_unenroll_cancel, null)
            .setPositiveButton(R.string.voiceprint_unenroll_confirm_action) { _, _ -> onUnenroll() }.show()
    }
    fun dismiss() { dialog.dismiss() }
}

package id.my.yhdev.nexo.ui

import android.transition.ChangeBounds
import android.transition.Fade
import android.transition.TransitionManager
import android.transition.TransitionSet
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.button.MaterialButton
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import id.my.yhdev.nexo.R
import id.my.yhdev.nexo.network.VoiceprintDialog

class VoiceprintManagementSheet(
    private val activity: AppCompatActivity,
    private var currentDialog: VoiceprintDialog?,
    private val onUnenroll: () -> Unit,
    private val onDismiss: () -> Unit
) {
    private val dialog = BottomSheetDialog(activity, R.style.ThemeOverlay_Nexo_CredentialSheet)
    private var rootView: View? = null
    private var ivSheetStatusLed: ImageView? = null
    private var tvVoiceprintCount: TextView? = null
    private var tvVoiceprintDetails: TextView? = null
    private var layoutVoiceprintMeta: LinearLayout? = null
    private var tvVoiceprintMetaText: TextView? = null
    private var btnVoiceprintEnroll: MaterialButton? = null
    private var btnVoiceprintUnenroll: MaterialButton? = null
    private var cardVoiceprintStatus: LinearLayout? = null
    private var cardEnrollHint: View? = null

    fun show() {
        val content = LayoutInflater.from(dialog.context).inflate(R.layout.sheet_voiceprint_management, null)
        rootView = content

        cardVoiceprintStatus = content.findViewById(R.id.cardVoiceprintStatus)
        cardEnrollHint = content.findViewById(R.id.cardEnrollHint)
        ivSheetStatusLed = content.findViewById(R.id.ivSheetStatusLed)
        tvVoiceprintCount = content.findViewById(R.id.tvVoiceprintCount)
        tvVoiceprintDetails = content.findViewById(R.id.tvVoiceprintDetails)
        layoutVoiceprintMeta = content.findViewById(R.id.layoutVoiceprintMeta)
        tvVoiceprintMetaText = content.findViewById(R.id.tvVoiceprintMetaText)
        btnVoiceprintEnroll = content.findViewById(R.id.btnVoiceprintEnroll)
        btnVoiceprintUnenroll = content.findViewById(R.id.btnVoiceprintUnenroll)

        btnVoiceprintEnroll?.setOnClickListener {
            // Pulse the hint card to direct focus
            cardEnrollHint?.animate()
                ?.scaleX(1.03f)
                ?.scaleY(1.03f)
                ?.setDuration(160)
                ?.withEndAction {
                    cardEnrollHint?.animate()?.scaleX(1.0f)?.scaleY(1.0f)?.setDuration(160)?.start()
                }
                ?.start()
            Toast.makeText(
                activity,
                "Ucapkan kalimat panduan ke mikrofon selama minimal 2 detik...",
                Toast.LENGTH_LONG
            ).show()
        }

        btnVoiceprintUnenroll?.setOnClickListener {
            showUnenrollConfirmation()
        }

        content.findViewById<MaterialButton>(R.id.btnVoiceprintClose).setOnClickListener {
            dialog.dismiss()
        }

        dialog.setOnDismissListener {
            onDismiss()
        }

        renderState(currentDialog)
        dialog.setContentView(content)
        dialog.show()

        // Subtle entrance scale animation for the status card
        cardVoiceprintStatus?.apply {
            alpha = 0f
            scaleX = 0.95f
            scaleY = 0.95f
            animate()
                .alpha(1f)
                .scaleX(1f)
                .scaleY(1f)
                .setDuration(300)
                .start()
        }
    }

    fun updateDialog(voiceDialog: VoiceprintDialog) {
        currentDialog = voiceDialog
        if (dialog.isShowing) {
            activity.runOnUiThread {
                renderState(voiceDialog)
            }
        }
    }

    private fun renderState(voiceDialog: VoiceprintDialog?) {
        val container = rootView as? ViewGroup ?: return
        val transition = TransitionSet().apply {
            ordering = TransitionSet.ORDERING_TOGETHER
            addTransition(ChangeBounds().setDuration(220))
            addTransition(Fade().setDuration(180))
        }
        TransitionManager.beginDelayedTransition(container, transition)

        val isEnrolled = voiceDialog?.isEnrolled == true
        val ledColor = if (isEnrolled) 0xFF00E676.toInt() else 0xFFFFB300.toInt()
        ivSheetStatusLed?.setColorFilter(ledColor)

        if (isEnrolled) {
            tvVoiceprintCount?.text = activity.getString(R.string.voiceprint_count_enrolled)
            tvVoiceprintDetails?.text = activity.getString(R.string.voiceprint_info_enrolled)
            btnVoiceprintEnroll?.setText(R.string.voiceprint_btn_re_enroll)
            btnVoiceprintUnenroll?.visibility = View.VISIBLE

            val meta = voiceDialog?.metadata
            if (meta != null) {
                layoutVoiceprintMeta?.visibility = View.VISIBLE
                val dur = meta.durationSeconds ?: 0f
                val at = meta.enrolledAt ?: "-"
                val speaker = meta.speaker ?: "owner"
                val metaInfo = "Profil: $speaker • Sampel: ${dur}s\nTerdaftar pada: $at"
                tvVoiceprintMetaText?.text = metaInfo
            } else {
                layoutVoiceprintMeta?.visibility = View.GONE
            }
        } else {
            tvVoiceprintCount?.text = activity.getString(R.string.voiceprint_count_open)
            tvVoiceprintDetails?.text = activity.getString(R.string.voiceprint_info_open)
            btnVoiceprintEnroll?.setText(R.string.voiceprint_btn_enroll)
            layoutVoiceprintMeta?.visibility = View.GONE
            btnVoiceprintUnenroll?.visibility = View.GONE
        }
    }

    private fun showUnenrollConfirmation() {
        MaterialAlertDialogBuilder(dialog.context)
            .setTitle(R.string.voiceprint_unenroll_confirm_title)
            .setMessage(R.string.voiceprint_unenroll_confirm_body)
            .setNegativeButton(R.string.voiceprint_unenroll_cancel, null)
            .setPositiveButton(R.string.voiceprint_unenroll_confirm_action) { _, _ ->
                // Animate card pulse during deletion
                cardVoiceprintStatus?.animate()
                    ?.scaleX(0.92f)
                    ?.scaleY(0.92f)
                    ?.alpha(0.6f)
                    ?.setDuration(180)
                    ?.withEndAction {
                        onUnenroll()
                        cardVoiceprintStatus?.animate()
                            ?.scaleX(1f)
                            ?.scaleY(1f)
                            ?.alpha(1f)
                            ?.setDuration(200)
                            ?.start()
                    }
                    ?.start()
                Toast.makeText(activity, R.string.voiceprint_unenroll_success, Toast.LENGTH_SHORT).show()
            }
            .create()
            .show()
    }

    fun dismiss() {
        if (dialog.isShowing) {
            dialog.dismiss()
        }
    }
}
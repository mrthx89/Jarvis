package id.my.yhdev.nexo.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.widget.CheckBox
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.doAfterTextChanged
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.google.android.material.bottomsheet.BottomSheetBehavior
import com.google.android.material.bottomsheet.BottomSheetDialog
import com.google.android.material.button.MaterialButton
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
import id.my.yhdev.nexo.R

/** Local UI only: never fetches, logs, restores, or copies a bearer token. */
class CredentialSetupSheet(
    private val activity: AppCompatActivity,
    private val deviceId: String,
    private val origin: String,
    private val isCurrentOrigin: () -> Boolean,
    private val onSave: (String) -> Unit,
    private val onReset: () -> Unit,
    private val onDismiss: () -> Unit
) {
    private val dialog = BottomSheetDialog(activity, R.style.ThemeOverlay_Nexo_CredentialSheet)
    private var resetConfirmation: androidx.appcompat.app.AlertDialog? = null

    fun show() {
        val content = LayoutInflater.from(dialog.context).inflate(R.layout.sheet_credential_setup, null)
        val input = content.findViewById<TextInputEditText>(R.id.setupToken)
        val field = content.findViewById<TextInputLayout>(R.id.setupTokenLayout)
        val trust = content.findViewById<CheckBox>(R.id.setupTrustOrigin)
        val save = content.findViewById<MaterialButton>(R.id.setupSave)
        val help = content.findViewById<TextView>(R.id.setupHelpBody)
        content.findViewById<TextView>(R.id.setupDeviceId).text = deviceId
        content.findViewById<TextView>(R.id.setupOrigin).text = origin
        // Defense in depth: do not persist credential text in hierarchy state/autofill.
        input.isSaveEnabled = false
        input.importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
        fun updateSave() {
            save.isEnabled = trust.isChecked && !input.text.isNullOrBlank()
        }
        input.doAfterTextChanged { field.error = null; updateSave() }
        trust.setOnCheckedChangeListener { _, _ -> updateSave() }
        updateSave()
        content.findViewById<MaterialButton>(R.id.setupCopyId).setOnClickListener {
            val clipboard = activity.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            clipboard.setPrimaryClip(ClipData.newPlainText("Nexo device ID", deviceId))
            Toast.makeText(activity, R.string.setup_id_copied, Toast.LENGTH_SHORT).show()
        }
        content.findViewById<MaterialButton>(R.id.setupHelp).setOnClickListener {
            help.visibility = if (help.visibility == View.VISIBLE) View.GONE else View.VISIBLE
        }
        save.setOnClickListener {
            val token = input.text?.toString()?.trim().orEmpty()
            when {
                !isCurrentOrigin() -> field.error = activity.getString(R.string.setup_stale_origin)
                !token.matches(Regex("[A-Za-z0-9_-]{43,128}")) ->
                    field.error = activity.getString(R.string.setup_invalid_token)
                !trust.isChecked -> trust.requestFocus()
                else -> try {
                    onSave(token)
                    Toast.makeText(activity, R.string.setup_saved, Toast.LENGTH_SHORT).show()
                    dismiss()
                } catch (_: Exception) {
                    field.error = activity.getString(R.string.setup_save_failed)
                }
            }
        }
        input.setOnEditorActionListener { _, action, _ ->
            if (action == EditorInfo.IME_ACTION_DONE) {
                if (save.isEnabled) save.performClick()
                true
            } else false
        }
        content.findViewById<MaterialButton>(R.id.setupCancel).setOnClickListener { dismiss() }
        content.findViewById<MaterialButton>(R.id.setupReset).setOnClickListener {
            if (resetConfirmation?.isShowing == true) return@setOnClickListener
            resetConfirmation = MaterialAlertDialogBuilder(dialog.context)
                .setTitle(R.string.setup_reset_title)
                .setMessage(R.string.setup_reset_body)
                .setNegativeButton(R.string.setup_back, null)
                .setPositiveButton(R.string.setup_reset_confirm) { _, _ ->
                    try {
                        onReset()
                        dismiss()
                    } catch (_: Exception) {
                        field.error = activity.getString(R.string.setup_reset_failed)
                    }
                }.create().also { confirmation ->
                    confirmation.window?.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
                    confirmation.show()
                }
        }

        // App Version & Buy Me a Coffee Support
        val versionView = content.findViewById<TextView>(R.id.setupAppVersion)
        val versionName = try {
            val pInfo = activity.packageManager.getPackageInfo(activity.packageName, 0)
            pInfo.versionName ?: "1.0.0"
        } catch (_: Exception) {
            "1.0.0"
        }
        versionView?.text = activity.getString(R.string.setup_app_version, versionName)

        content.findViewById<View>(R.id.setupSupportButton)?.setOnClickListener {
            try {
                val intent = Intent(Intent.ACTION_VIEW, Uri.parse("https://yapp.ink/yhdev"))
                activity.startActivity(intent)
            } catch (_: Exception) {
                Toast.makeText(activity, "Buka https://yapp.ink/yhdev di browser Anda.", Toast.LENGTH_LONG).show()
            }
        }

        val leftPadding = content.paddingLeft
        val topPadding = content.paddingTop
        val rightPadding = content.paddingRight
        val bottomPadding = content.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(content) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout()
            )
            // Absolute base padding avoids accumulation on keyboard/inset updates.
            // ADJUST_RESIZE handles the IME; do not add its height again here.
            view.setPadding(
                leftPadding + bars.left, topPadding + bars.top,
                rightPadding + bars.right, bottomPadding + bars.bottom
            )
            insets
        }
        dialog.setContentView(content)
        dialog.window?.apply {
            addFlags(WindowManager.LayoutParams.FLAG_SECURE)
            setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        }
        dialog.setOnShowListener {
            // A bounded viewport lets the form scroll instead of measuring taller
            // than a phone window. It shrinks with the keyboard via ADJUST_RESIZE.
            dialog.findViewById<View>(com.google.android.material.R.id.design_bottom_sheet)?.let { sheet ->
                val displayMetrics = activity.resources.displayMetrics
                val maxSheetWidth = (560 * displayMetrics.density).toInt()
                val isTablet = displayMetrics.widthPixels > maxSheetWidth
                sheet.layoutParams = sheet.layoutParams.apply {
                    height = ViewGroup.LayoutParams.MATCH_PARENT
                    if (isTablet) {
                        width = maxSheetWidth
                    }
                }
                sheet.setBackgroundResource(R.drawable.bg_bottom_sheet_modal)
            }
            dialog.behavior.apply {
                skipCollapsed = true
                state = BottomSheetBehavior.STATE_EXPANDED
            }
            // Keep token field unfocused until deliberately tapped, preserving context.
            content.isFocusableInTouchMode = true
            content.requestFocus()
        }
        dialog.setOnDismissListener {
            input.text?.clear()
            resetConfirmation?.dismiss()
            resetConfirmation = null
            onDismiss()
        }
        dialog.show()
        ViewCompat.requestApplyInsets(content)
    }

    fun dismiss() { dialog.dismiss() }
}

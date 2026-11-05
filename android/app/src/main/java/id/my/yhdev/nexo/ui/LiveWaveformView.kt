package id.my.yhdev.nexo.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.os.SystemClock
import android.util.AttributeSet
import android.view.View
import kotlin.math.sin

/**
 * Living equalizer waveform status indicator inspired by Google Gemini Live.
 * Renders 4 dynamic rounded vertical bars that dance organically to microphone telemetry.
 */
class LiveWaveformView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = 0xFF00E5FF.toInt() // Nexo Cyan (matches Gemini accent)
        style = Paint.Style.FILL
    }

    private val rect = RectF()
    private val barCount = 4
    private val barHeights = FloatArray(barCount) { 4f }
    private val targetHeights = FloatArray(barCount) { 4f }
    private val barMultipliers = floatArrayOf(0.70f, 1.0f, 0.85f, 0.60f)
    private val barPhases = floatArrayOf(0.0f, 1.2f, 2.4f, 3.6f)

    private var active = false
    private var currentAmplitude = 0f
    private var lastFrameTime = SystemClock.uptimeMillis()

    fun setActive(isActive: Boolean) {
        if (active == isActive) return
        active = isActive
        if (!isActive) {
            currentAmplitude = 0f
            for (i in 0 until barCount) targetHeights[i] = 4f
        }
        postInvalidateOnAnimation()
    }

    fun updateAmplitude(amplitude: Float) {
        currentAmplitude = amplitude.coerceIn(0f, 1f)
        if (active) {
            postInvalidateOnAnimation()
        }
    }

    fun setWaveColor(color: Int) {
        barPaint.color = color
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val now = SystemClock.uptimeMillis()
        val dt = ((now - lastFrameTime).coerceIn(1L, 50L)) / 1000f
        lastFrameTime = now

        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0f || h <= 0f) return

        val minBarHeight = (h * 0.18f).coerceAtLeast(4f)
        val maxBarHeight = (h * 0.88f).coerceAtLeast(minBarHeight)

        // Compute dynamic targets for each bar
        val timeSec = now / 350.0
        for (i in 0 until barCount) {
            if (active) {
                // Subtle organic idle wave + lively reaction to mic amplitude
                val waveOffset = (sin(timeSec + barPhases[i]) * 0.15f + 0.15f).toFloat()
                val ampFactor = (currentAmplitude * 1.5f).coerceIn(0f, 1f)
                val combined = (waveOffset + ampFactor * barMultipliers[i]).coerceIn(0f, 1f)
                targetHeights[i] = minBarHeight + (maxBarHeight - minBarHeight) * combined
            } else {
                targetHeights[i] = minBarHeight
            }

            // Smooth interpolation (spring-like responsiveness)
            val lerpSpeed = if (targetHeights[i] > barHeights[i]) 14f else 8f
            barHeights[i] += (targetHeights[i] - barHeights[i]) * (lerpSpeed * dt).coerceIn(0f, 1f)
        }

        // Layout the 4 bars symmetrically centered in the view
        val barWidth = (w / (barCount * 2f)).coerceIn(3f, 8f)
        val totalBarsWidth = barCount * barWidth
        val totalSpacing = (w - totalBarsWidth)
        val spacing = totalSpacing / (barCount + 1).coerceAtLeast(1)
        val cornerRadius = barWidth / 2f
        val centerY = h / 2f

        for (i in 0 until barCount) {
            val left = spacing + i * (barWidth + spacing)
            val right = left + barWidth
            val halfBarH = barHeights[i] / 2f
            val top = (centerY - halfBarH).coerceAtLeast(0f)
            val bottom = (centerY + halfBarH).coerceAtMost(h)

            rect.set(left, top, right, bottom)
            canvas.drawRoundRect(rect, cornerRadius, cornerRadius, barPaint)
        }

        // Keep continuous 60fps animation loop while active
        if (active || barHeights.any { it > minBarHeight + 0.5f }) {
            postInvalidateOnAnimation()
        }
    }
}

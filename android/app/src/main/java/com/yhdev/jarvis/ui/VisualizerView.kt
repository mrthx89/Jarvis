package com.yhdev.jarvis.ui

import android.content.Context
import android.graphics.*
import android.util.AttributeSet
import android.view.View
import kotlin.math.cos
import kotlin.math.sin

class VisualizerView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private var currentState: JarvisState = JarvisState.IDLE
    private var targetAmplitude: Float = 0f
    private var smoothAmplitude: Float = 0f
    private var animationPhase: Float = 0f

    private val corePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }

    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 4f
    }

    private val glowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 12f
    }

    fun setState(state: JarvisState) {
        this.currentState = state
        postInvalidateOnAnimation()
    }

    fun setAmplitude(amp: Float) {
        this.targetAmplitude = amp.coerceIn(0f, 1f)
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val cx = width / 2f
        val cy = height / 2f
        val baseRadius = minOf(cx, cy) * 0.45f

        // Smooth interpolation for fluid organic feel
        smoothAmplitude += (targetAmplitude - smoothAmplitude) * 0.15f
        animationPhase += 0.05f
        if (animationPhase > Math.PI * 2) {
            animationPhase = 0f
        }

        // Color palette based on state
        val (coreColor, glowColor) = when (currentState) {
            JarvisState.IDLE -> Pair(0xFF00E5FF.toInt(), 0x3300E5FF.toInt())
            JarvisState.LISTENING -> Pair(0xFF00E676.toInt(), 0x4400E676.toInt())
            JarvisState.THINKING -> Pair(0xFFFFB300.toInt(), 0x44FFB300.toInt())
            JarvisState.SPEAKING -> Pair(0xFF00E5FF.toInt(), 0x6600E5FF.toInt())
            JarvisState.ERROR -> Pair(0xFFFF5252.toInt(), 0x44FF5252.toInt())
            JarvisState.DISCONNECTED -> Pair(0xFF555555.toInt(), 0x22555555.toInt())
        }

        // Dynamic radius calculation
        val breath = sin(animationPhase.toDouble()).toFloat() * 6f
        val reactiveRadius = baseRadius + breath + (smoothAmplitude * baseRadius * 0.4f)

        // Draw Outer Soft Glow
        glowPaint.color = glowColor
        canvas.drawCircle(cx, cy, reactiveRadius + 18f, glowPaint)

        // Draw Middle Resonant Ring
        ringPaint.color = coreColor
        ringPaint.alpha = (180 + (smoothAmplitude * 75)).toInt().coerceIn(0, 255)
        canvas.drawCircle(cx, cy, reactiveRadius, ringPaint)

        // Draw Thinking Orbiters if in THINKING state
        if (currentState == JarvisState.THINKING) {
            val orbitRadius = reactiveRadius + 24f
            for (i in 0 until 3) {
                val angle = animationPhase * 2f + (i * Math.PI * 2 / 3).toFloat()
                val ox = cx + cos(angle.toDouble()).toFloat() * orbitRadius
                val oy = cy + sin(angle.toDouble()).toFloat() * orbitRadius
                corePaint.color = coreColor
                canvas.drawCircle(ox, oy, 5f, corePaint)
            }
        }

        // Draw Inner Dense Core
        corePaint.color = coreColor
        corePaint.alpha = (220 + (smoothAmplitude * 35)).toInt().coerceIn(0, 255)
        canvas.drawCircle(cx, cy, reactiveRadius * 0.65f, corePaint)

        // Continuous 60 FPS animation frame request
        postInvalidateOnAnimation()
    }
}

package com.yhdev.jarvis.ui

import android.content.Context
import android.graphics.*
import android.os.SystemClock
import android.util.AttributeSet
import android.view.View
import kotlin.math.cos
import kotlin.math.sin
import kotlin.random.Random

/**
 * Nusa-Inspired Living Mascot Face for Jarvis.
 * Features animated glowing expressive eyes with natural blinking,
 * micro-saccade glances, and state-reactive emotions (listening, thinking, speaking).
 */
class VisualizerView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private var currentState: JarvisState = JarvisState.IDLE
    private var targetAmplitude: Float = 0f
    private var smoothAmplitude: Float = 0f

    // Pre-allocated graphic objects to prevent GC in animation loop
    private val leftEyeRect = RectF()
    private val rightEyeRect = RectF()
    private val glowRect = RectF()

    private val eyeCorePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
        color = Color.WHITE
    }

    private val eyeGlowInnerPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 8f
    }

    private val eyeGlowOuterPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 24f
    }

    private val particlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }

    // Animation Timers & States
    private var lastFrameTime = SystemClock.uptimeMillis()
    private var blinkStartTime = 0L
    private var nextBlinkTime = SystemClock.uptimeMillis() + 3000L
    private var isBlinking = false

    // Gaze Direction (Micro-saccades)
    private var gazeTargetX = 0f
    private var gazeTargetY = 0f
    private var gazeCurrentX = 0f
    private var gazeCurrentY = 0f
    private var nextGlanceTime = SystemClock.uptimeMillis() + 4000L

    // Continuous breath phase
    private var breathPhase = 0f

    fun setState(state: JarvisState) {
        if (this.currentState != state) {
            this.currentState = state
            onStateTransition(state)
        }
        postInvalidateOnAnimation()
    }

    fun setAmplitude(amp: Float) {
        this.targetAmplitude = amp.coerceIn(0f, 1f)
    }

    private fun onStateTransition(state: JarvisState) {
        when (state) {
            JarvisState.LISTENING -> {
                // Look straight and alert
                gazeTargetX = 0f
                gazeTargetY = -4f
            }
            JarvisState.THINKING -> {
                // Glance upward-right (thoughtful look)
                gazeTargetX = 14f
                gazeTargetY = -12f
            }
            JarvisState.SPEAKING -> {
                gazeTargetX = 0f
                gazeTargetY = 0f
            }
            JarvisState.IDLE -> {
                gazeTargetX = 0f
                gazeTargetY = 0f
            }
            JarvisState.ERROR, JarvisState.DISCONNECTED -> {
                gazeTargetX = 0f
                gazeTargetY = 8f
            }
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val now = SystemClock.uptimeMillis()
        val dt = (now - lastFrameTime).coerceIn(1L, 100L) / 1000f
        lastFrameTime = now

        // Smooth amplitude lerp for organic audio responsiveness
        smoothAmplitude += (targetAmplitude - smoothAmplitude) * 0.2f

        // Update breath oscillation
        breathPhase += dt * 2.5f
        if (breathPhase > Math.PI * 2) breathPhase -= (Math.PI * 2).toFloat()
        val breathDelta = sin(breathPhase.toDouble()).toFloat()

        // Handle Natural Blinking
        if (!isBlinking && now >= nextBlinkTime && currentState != JarvisState.LISTENING) {
            isBlinking = true
            blinkStartTime = now
        }

        var blinkScaleY = 1.0f
        if (isBlinking) {
            val blinkElapsed = (now - blinkStartTime).toFloat()
            val blinkDuration = 160f // ms total blink cycle
            if (blinkElapsed < blinkDuration) {
                val progress = blinkElapsed / blinkDuration
                // Parabolic curve: 1.0 -> 0.08 -> 1.0
                blinkScaleY = (4f * (progress - 0.5f) * (progress - 0.5f)).coerceIn(0.08f, 1.0f)
            } else {
                isBlinking = false
                nextBlinkTime = now + Random.nextLong(3000L, 5500L)
            }
        }

        // Handle Saccade Look-around during IDLE
        if (currentState == JarvisState.IDLE && now >= nextGlanceTime) {
            gazeTargetX = Random.nextInt(-16, 17).toFloat()
            gazeTargetY = Random.nextInt(-8, 9).toFloat()
            nextGlanceTime = now + Random.nextLong(3500L, 6000L)
        }

        // Smooth gaze interpolation
        gazeCurrentX += (gazeTargetX - gazeCurrentX) * (dt * 6f)
        gazeCurrentY += (gazeTargetY - gazeCurrentY) * (dt * 6f)

        // Palette configuration
        val (coreColor, glowInnerColor, glowOuterColor) = when (currentState) {
            JarvisState.IDLE -> Triple(
                0xFFFFFFFF.toInt(),
                0x9978BEFF.toInt(), // Electric cyan-blue
                0x44508CFF.toInt()
            )
            JarvisState.LISTENING -> Triple(
                0xFFFFFFFF.toInt(),
                0xCC00E5FF.toInt(), // Vibrant attentive cyan
                0x6600E676.toInt()  // Emerald aura
            )
            JarvisState.THINKING -> Triple(
                0xFFFFF8E1.toInt(),
                0xBBFFB300.toInt(), // Amber / Gold calculation
                0x44FF8F00.toInt()
            )
            JarvisState.SPEAKING -> Triple(
                0xFFFFFFFF.toInt(),
                0xDD00E5FF.toInt(),
                0x772979FF.toInt()
            )
            JarvisState.ERROR -> Triple(
                0xFFFFEBEE.toInt(),
                0xBBFF5252.toInt(),
                0x44D50000.toInt()
            )
            JarvisState.DISCONNECTED -> Triple(
                0xFF888888.toInt(),
                0x44666666.toInt(),
                0x22333333.toInt()
            )
        }

        eyeCorePaint.color = coreColor
        eyeGlowInnerPaint.color = glowInnerColor
        eyeGlowOuterPaint.color = glowOuterColor

        // Geometry dimensions scaled to View size
        val cx = width / 2f + gazeCurrentX
        val cy = height / 2f + gazeCurrentY + (breathDelta * 2f)

        // Base eye dimensions (capsule / rounded pill)
        val eyeWidth = width * 0.085f
        var eyeHeight = width * 0.135f
        val eyeSpacing = width * 0.12f // distance from center to each eye
        val cornerRadius = eyeWidth / 2f

        // State dynamic modulations
        when (currentState) {
            JarvisState.LISTENING -> {
                // Wide attentive eyes modulated by user voice energy
                eyeHeight *= (1.18f + smoothAmplitude * 0.25f)
            }
            JarvisState.SPEAKING -> {
                // Dynamic rhythmic voice bounce
                val bounce = smoothAmplitude * (eyeHeight * 0.35f)
                eyeHeight += bounce
            }
            JarvisState.DISCONNECTED -> {
                // Sleepy / drooping eyelids
                eyeHeight *= 0.65f
            }
            else -> {}
        }

        // Apply Blink Scale
        val currentEyeHeight = (eyeHeight * blinkScaleY).coerceAtLeast(4f)
        val halfW = eyeWidth / 2f
        val halfH = currentEyeHeight / 2f

        // Draw Left Eye
        val leftCenterX = cx - eyeSpacing
        leftEyeRect.set(leftCenterX - halfW, cy - halfH, leftCenterX + halfW, cy + halfH)
        drawLivingEye(canvas, leftEyeRect, cornerRadius)

        // Draw Right Eye
        val rightCenterX = cx + eyeSpacing
        rightEyeRect.set(rightCenterX - halfW, cy - halfH, rightCenterX + halfW, cy + halfH)
        drawLivingEye(canvas, rightEyeRect, cornerRadius)

        // Extra Thinking Orbit Particles
        if (currentState == JarvisState.THINKING) {
            particlePaint.color = glowInnerColor
            val orbitR = eyeSpacing * 2.1f
            for (i in 0 until 4) {
                val angle = breathPhase * 3f + (i * Math.PI * 0.5f).toFloat()
                val px = cx + cos(angle.toDouble()).toFloat() * orbitR
                val py = cy + sin(angle.toDouble()).toFloat() * (orbitR * 0.45f)
                canvas.drawCircle(px, py, 4f, particlePaint)
            }
        }

        // Keep 60 FPS smooth rendering loop active
        postInvalidateOnAnimation()
    }

    private fun drawLivingEye(canvas: Canvas, rect: RectF, radius: Float) {
        // Outer ambient glow
        glowRect.set(rect.left - 16f, rect.top - 16f, rect.right + 16f, rect.bottom + 16f)
        canvas.drawRoundRect(glowRect, radius + 12f, radius + 12f, eyeGlowOuterPaint)

        // Inner sharp glow
        glowRect.set(rect.left - 6f, rect.top - 6f, rect.right + 6f, rect.bottom + 6f)
        canvas.drawRoundRect(glowRect, radius + 4f, radius + 4f, eyeGlowInnerPaint)

        // Core bright eye capsule
        canvas.drawRoundRect(rect, radius, radius, eyeCorePaint)
    }
}

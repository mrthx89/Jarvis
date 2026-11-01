package id.my.yhdev.nexo.ui

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.os.SystemClock
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import androidx.core.content.ContextCompat
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.random.Random

/**
 * Living Mascot View with Organic Physics-based Transitions,
 * Living Digital Glowing Eyes (Radial Gradient Layer),
 * Organic Blinking Kinetics & Physiological Micro-saccades,
 * OLED/IPS Burn-In Protection Pixel-Shifting, and Ambient Sleep Mode.
 */
class VisualizerView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : View(context, attrs, defStyleAttr) {

    private var voiceEnabled = false
    private var voiceReactionUntil = 0L
    private var stateExpression = MascotExpression.NETRAL
    private var conversationExpression: MascotExpression? = null
    private var conversationExpressionUntil = 0L
    private var currentState: NexoState = NexoState.IDLE
    private var targetAmplitude: Float = 0f
    private var smoothAmplitude: Float = 0f

    // Expression States & Transitions
    private var currentExpression: MascotExpression = MascotExpression.NETRAL
    private var previousExpression: MascotExpression = MascotExpression.NETRAL
    private var isTransitioning = false
    private var transitionStartTime = 0L
    private val transitionDuration = 320f // ms physics spring morph

    // Pre-cached Bitmaps for instantaneous rendering without GC pauses
    private val bitmapCache = mutableMapOf<MascotExpression, Bitmap>()

    // Paints
    private val srcPaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val dstPaint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val auraPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }
    private val shadowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }
    private val eyeGlowPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.FILL
    }

    // Transformation Rects & Matrix
    private val drawRect = RectF()
    private val shadowRect = RectF()
    private val animMatrix = Matrix()
    private val eyeRectLeft = RectF()
    private val eyeRectRight = RectF()

    // Physics Animation Properties
    private var lastFrameTime = SystemClock.uptimeMillis()
    private var breathPhase = 0f

    // Micro-saccades (Drift & Tilt)
    private var gazeTargetX = 0f
    private var gazeTargetY = 0f
    private var gazeCurrentX = 0f
    private var gazeCurrentY = 0f
    private var tiltTargetDeg = 0f
    private var tiltCurrentDeg = 0f
    private var nextGlanceTime = SystemClock.uptimeMillis() + 3500L

    // Organic Blink Kinetics
    private var nextBlinkTime = SystemClock.uptimeMillis() + 3000L
    private var isBlinking = false
    private var blinkStartTime = 0L

    // Inactivity & Ambient Sleepiness Timer (5 minutes = 300_000 ms)
    private var idleStartTime = SystemClock.uptimeMillis()
    private var ambientDimMultiplier = 1.0f

    // Pixel-Shifting for OLED/IPS Burn-In Protection (2-3px every 3-5 minutes)
    private val pixelShiftOffsets = arrayOf(
        0f to 0f,
        2f to 0f,
        2f to 2f,
        0f to 2f,
        -2f to 1f,
        -1f to -2f,
        1f to -2f,
        2f to -1f
    )
    private var pixelShiftIndex = 0
    private var nextPixelShiftTime = SystemClock.uptimeMillis() + 210_000L // 3.5 minutes
    private var currentShiftX = 0f
    private var currentShiftY = 0f
    private var targetShiftX = 0f
    private var targetShiftY = 0f

    init {
        // Pre-warm bitmap cache in view layout
        post {
            loadBitmaps()
        }
    }

    private fun loadBitmaps() {
        val targetSize = (minOf(width, height) * 0.9f).toInt().coerceAtLeast(256)
        for (expr in MascotExpression.entries) {
            try {
                val drawable = ContextCompat.getDrawable(context, expr.drawableRes) ?: continue
                val bmp = Bitmap.createBitmap(targetSize, targetSize, Bitmap.Config.ARGB_8888)
                val canvas = Canvas(bmp)
                drawable.setBounds(0, 0, targetSize, targetSize)
                drawable.draw(canvas)
                bitmapCache[expr] = bmp
            } catch (e: Exception) {
                // Graceful fallback
            }
        }
        postInvalidateOnAnimation()
    }

    fun setState(state: NexoState, expressionName: String? = null) {
        val now = SystemClock.uptimeMillis()
        val previousState = currentState
        if (voiceEnabled && (state == NexoState.IDLE || state == NexoState.LISTENING) &&
            previousState == NexoState.SPEAKING) {
            // Preserve a reply emotion briefly after the server finishes sending audio.
            conversationExpression = stateExpression.takeUnless { it == MascotExpression.NETRAL }
            conversationExpressionUntil = now + 6000L
        } else if (state != NexoState.IDLE && state != NexoState.LISTENING) {
            conversationExpression = null
            conversationExpressionUntil = 0L
        }
        this.currentState = state

        if (state == NexoState.IDLE && previousState != NexoState.IDLE) {
            idleStartTime = now
        }

        val targetExpr = if (expressionName != null) {
            MascotExpression.fromString(expressionName)
        } else {
            when (state) {
                NexoState.IDLE -> MascotExpression.NETRAL
                NexoState.LISTENING -> MascotExpression.NETRAL
                NexoState.THINKING -> MascotExpression.BINGUNG
                NexoState.SPEAKING -> MascotExpression.SENANG
                NexoState.ERROR -> MascotExpression.SEDIH
                NexoState.DISCONNECTED -> MascotExpression.MENGANTUK
            }
        }

        stateExpression = targetExpr
        updateExpression(SystemClock.uptimeMillis())
        onStatePhysics(state)
        postInvalidateOnAnimation()
    }

    /** Visual feedback only; this view never controls microphone or networking. */
    fun setVoiceEnabled(enabled: Boolean, animateReaction: Boolean) {
        val now = SystemClock.uptimeMillis()
        val wasEnabled = voiceEnabled
        voiceEnabled = enabled
        voiceReactionUntil = when {
            !enabled -> 0L
            !wasEnabled && animateReaction -> now + 2000L
            else -> voiceReactionUntil
        }
        if (!enabled) {
            conversationExpression = null
            conversationExpressionUntil = 0L
            setAmplitude(0f)
        }
        updateExpression(now)
        postInvalidateOnAnimation()
    }

    private fun updateExpression(now: Long) {
        val expression = when {
            !voiceEnabled -> MascotExpression.MENANGIS
            now < voiceReactionUntil -> MascotExpression.SANGAT_SENANG
            (currentState == NexoState.IDLE || currentState == NexoState.LISTENING) &&
                now < conversationExpressionUntil ->
                conversationExpression ?: stateExpression
            currentState == NexoState.IDLE && now - idleStartTime > 300000L &&
                stateExpression == MascotExpression.NETRAL -> MascotExpression.SLEEPING
            currentState == NexoState.IDLE && now - idleStartTime > 60000L &&
                stateExpression == MascotExpression.NETRAL -> MascotExpression.MENGANTUK
            else -> stateExpression
        }
        transitionToExpression(expression)
        if (voiceEnabled && now >= voiceReactionUntil) voiceReactionUntil = 0L
    }

    fun transitionToExpression(newExpr: MascotExpression) {
        // Repeated state/frame updates must not restart a cross-fade in progress.
        if (currentExpression == newExpr) return
        previousExpression = currentExpression
        currentExpression = newExpr
        isTransitioning = true
        transitionStartTime = SystemClock.uptimeMillis()
    }

    fun setAmplitude(amp: Float) {
        this.targetAmplitude = amp.coerceIn(0f, 1f)
    }

    private fun onStatePhysics(state: NexoState) {
        when (state) {
            NexoState.LISTENING -> {
                gazeTargetX = 0f
                gazeTargetY = -8f
                tiltTargetDeg = 0f
            }
            NexoState.THINKING -> {
                gazeTargetX = 16f
                gazeTargetY = -14f
                tiltTargetDeg = 3.5f // Inquisitive head tilt
            }
            NexoState.SPEAKING -> {
                gazeTargetX = 0f
                gazeTargetY = 0f
                tiltTargetDeg = 0f
            }
            NexoState.IDLE -> {
                gazeTargetX = 0f
                gazeTargetY = 0f
                tiltTargetDeg = 0f
            }
            NexoState.ERROR, NexoState.DISCONNECTED -> {
                gazeTargetX = 0f
                gazeTargetY = 10f
                tiltTargetDeg = -2.0f
            }
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)

        val now = SystemClock.uptimeMillis()
        updateExpression(now)
        val dt = (now - lastFrameTime).coerceIn(1L, 66L) / 1000f
        lastFrameTime = now

        // Smooth Audio Amplitude (lerp)
        smoothAmplitude += (targetAmplitude - smoothAmplitude) * 0.22f

        // Pixel-Shifting Orbit for OLED/IPS Burn-In Protection
        if (now >= nextPixelShiftTime) {
            pixelShiftIndex = (pixelShiftIndex + 1) % pixelShiftOffsets.size
            val nextShift = pixelShiftOffsets[pixelShiftIndex]
            targetShiftX = nextShift.first
            targetShiftY = nextShift.second
            nextPixelShiftTime = now + 210_000L
        }
        currentShiftX += (targetShiftX - currentShiftX) * (dt * 0.8f)
        currentShiftY += (targetShiftY - currentShiftY) * (dt * 0.8f)

        // Ambient Sleep Mode Dimming (> 5 minutes idle or sleeping expression)
        val isAmbientSleeping = currentState == NexoState.IDLE &&
            (currentExpression == MascotExpression.SLEEPING || now - idleStartTime > 300000L)
        val targetDim = if (isAmbientSleeping) 0.48f else 1.0f
        ambientDimMultiplier += (targetDim - ambientDimMultiplier) * (dt * 1.5f)

        // Continuous Organic Breathing Oscillation
        val breathSpeed = if (isAmbientSleeping) 1.2f else 2.2f
        breathPhase += dt * breathSpeed
        if (breathPhase > Math.PI * 2) breathPhase -= (Math.PI * 2).toFloat()
        val reactionBob = when {
            !voiceEnabled -> sin(now / 150.0).toFloat() * 2f
            now < voiceReactionUntil -> -kotlin.math.abs(sin(now / 180.0)).toFloat() * 8f
            else -> 0f
        }
        val breathFloatY = sin(breathPhase.toDouble()).toFloat() * (if (isAmbientSleeping) 2.5f else 5f) + reactionBob
        val breathScale = 1.0f + (sin(breathPhase.toDouble()).toFloat() * 0.012f)

        // Random Saccade Glances & Physiological Micro-saccades during IDLE
        if (currentState == NexoState.IDLE && !isAmbientSleeping && now >= nextGlanceTime) {
            gazeTargetX = Random.nextInt(-14, 15).toFloat()
            gazeTargetY = Random.nextInt(-6, 7).toFloat()
            tiltTargetDeg = Random.nextFloat() * 4f - 2f
            nextGlanceTime = now + Random.nextLong(3200L, 6500L)
        } else if (isAmbientSleeping) {
            gazeTargetX = 0f
            gazeTargetY = 2f
            tiltTargetDeg = 0f
        }

        // High-frequency physiological micro-tremor (0.6 - 1.2px)
        val microTremorX = if (!isAmbientSleeping) {
            (sin(now * 0.007).toFloat() * 0.9f + sin(now * 0.014).toFloat() * 0.4f)
        } else 0f
        val microTremorY = if (!isAmbientSleeping) {
            (cos(now * 0.009).toFloat() * 0.6f)
        } else 0f

        // Organic Blinking Kinetics (asymmetric rapid drop, brief hold, smooth ease-out)
        if (!isBlinking && now >= nextBlinkTime && currentState != NexoState.LISTENING && !isAmbientSleeping) {
            isBlinking = true
            blinkStartTime = now
        }

        var blinkSquashY = 1.0f
        if (isAmbientSleeping) {
            blinkSquashY = 0.08f
            isBlinking = false
        } else if (isBlinking) {
            val blinkElapsed = (now - blinkStartTime).toFloat()
            val blinkDuration = 160f
            if (blinkElapsed < blinkDuration) {
                val t = blinkElapsed / blinkDuration
                blinkSquashY = when {
                    t < 0.28f -> { // Fast eyelid drop (45ms)
                        val p = t / 0.28f
                        1.0f - (1.0f - 0.08f) * (p * p)
                    }
                    t < 0.44f -> 0.08f // Brief closed hold (25ms)
                    else -> { // Smooth gradual reopen (90ms)
                        val p = (t - 0.44f) / 0.56f
                        0.08f + (1.0f - 0.08f) * (1f - (1f - p) * (1f - p))
                    }
                }
            } else {
                isBlinking = false
                // Natural double-blink chance (18% probability of rapid secondary flutter)
                if (Random.nextFloat() < 0.18f && currentState == NexoState.IDLE) {
                    nextBlinkTime = now + Random.nextLong(180L, 340L)
                } else {
                    nextBlinkTime = now + Random.nextLong(3200L, 6200L)
                }
            }
        }

        // Smooth Critically Damped Gaze & Tilt Interpolation
        gazeCurrentX += (gazeTargetX + microTremorX - gazeCurrentX) * (dt * 6.5f)
        gazeCurrentY += (gazeTargetY + microTremorY - gazeCurrentY) * (dt * 6.5f)
        tiltCurrentDeg += (tiltTargetDeg - tiltCurrentDeg) * (dt * 4.2f)

        // Physics-Based Spring Interpolator for Expression Transitions
        var transProgress = 1.0f
        var squashStretchX = 1.0f
        var squashStretchY = 1.0f

        if (isTransitioning) {
            val elapsed = (now - transitionStartTime).toFloat()
            if (elapsed < transitionDuration) {
                val t = (elapsed / transitionDuration).coerceIn(0f, 1f)
                transProgress = evaluateSpringProgress(t)

                // Organic Anticipation Squash & Stretch Physics
                if (t < 0.38f) {
                    val p = t / 0.38f
                    val dip = sin(p * Math.PI).toFloat() * 0.085f
                    squashStretchY -= dip
                    squashStretchX += dip * 0.72f
                } else if (t < 0.82f) {
                    val p = (t - 0.38f) / 0.44f
                    val pop = sin(p * Math.PI).toFloat() * 0.095f
                    squashStretchY += pop
                    squashStretchX -= pop * 0.65f
                }
            } else {
                isTransitioning = false
                previousExpression = currentExpression
                transProgress = 1.0f
            }
        }

        // Audio Vocal Bounce during Speaking
        var speechBounceY = 0f
        var speechScale = 1.0f
        if (currentState == NexoState.SPEAKING) {
            speechBounceY = -smoothAmplitude * 18f
            speechScale = 1.0f + (smoothAmplitude * 0.08f)
        }

        // Attentive lean forward during Listening
        var listenScale = 1.0f
        if (currentState == NexoState.LISTENING) {
            listenScale = 1.08f + (smoothAmplitude * 0.05f)
        }

        // Calculate Final Geometry with Pixel-Shifting Offset
        val viewW = width.toFloat()
        val viewH = height.toFloat()
        val cx = viewW / 2f + gazeCurrentX + currentShiftX
        val cy = viewH / 2f + gazeCurrentY + breathFloatY + speechBounceY + currentShiftY

        val mascotSize = minOf(viewW, viewH) * 0.82f
        val finalScaleX = breathScale * squashStretchX * speechScale * listenScale
        val finalScaleY = breathScale * squashStretchY * speechScale * listenScale * blinkSquashY

        // Draw 3D Perspective Floor Shadow under character
        val floorY = cy + (mascotSize * 0.44f)
        val shadowW = mascotSize * 0.38f * (1f / finalScaleY.coerceAtLeast(0.5f))
        val shadowH = mascotSize * 0.08f * (1f / finalScaleY.coerceAtLeast(0.5f))
        shadowRect.set(cx - shadowW, floorY - shadowH, cx + shadowW, floorY + shadowH)
        val shadowAlpha = (0x66 * ambientDimMultiplier).toInt()
        shadowPaint.shader = RadialGradient(
            cx, floorY, shadowW.coerceAtLeast(10f),
            intArrayOf((shadowAlpha shl 24), 0x00000000),
            floatArrayOf(0.15f, 1.0f),
            Shader.TileMode.CLAMP
        )
        canvas.drawOval(shadowRect, shadowPaint)

        // Draw Ambient Lighting Aura behind mascot
        drawAmbientAura(canvas, cx, cy, mascotSize * 0.65f, isAmbientSleeping)

        // Setup Matrix for rotation, scale, and translation
        animMatrix.reset()
        animMatrix.postTranslate(-mascotSize / 2f, -mascotSize / 2f)
        animMatrix.postScale(finalScaleX, finalScaleY)
        animMatrix.postRotate(tiltCurrentDeg)
        animMatrix.postTranslate(cx, cy)

        val prevBmp = bitmapCache[previousExpression]
        val currBmp = bitmapCache[currentExpression]
        val baseAlpha = (255 * ambientDimMultiplier).toInt().coerceIn(0, 255)

        if (isTransitioning && prevBmp != null && currBmp != null) {
            // Smooth Cross-Dissolve Alpha Blending with Spring Physics
            val prevAlpha = (((1f - transProgress) * baseAlpha)).toInt().coerceIn(0, 255)
            val currAlpha = ((transProgress * baseAlpha)).toInt().coerceIn(0, 255)

            // Draw Outgoing Expression
            srcPaint.alpha = prevAlpha
            canvas.save()
            canvas.concat(animMatrix)
            drawRect.set(0f, 0f, mascotSize, mascotSize)
            canvas.drawBitmap(prevBmp, null, drawRect, srcPaint)
            canvas.restore()

            // Draw Incoming Expression
            dstPaint.alpha = currAlpha
            canvas.save()
            canvas.concat(animMatrix)
            canvas.drawBitmap(currBmp, null, drawRect, dstPaint)
            canvas.restore()
        } else {
            // Draw Stable Expression
            val bmpToDraw = currBmp ?: prevBmp
            if (bmpToDraw != null) {
                srcPaint.alpha = baseAlpha
                canvas.save()
                canvas.concat(animMatrix)
                drawRect.set(0f, 0f, mascotSize, mascotSize)
                canvas.drawBitmap(bmpToDraw, null, drawRect, srcPaint)
                canvas.restore()
            }
        }

        // Draw Living Digital Glowing Eyes (Radial Gradient Layer on Pupil/Iris)
        if (currentExpression != MascotExpression.SLEEPING && blinkSquashY > 0.15f) {
            drawLivingEyeGlow(canvas, mascotSize, finalScaleX, finalScaleY, blinkSquashY, isAmbientSleeping)
        }

        // Continuous 60 FPS Frame Loop
        postInvalidateOnAnimation()
    }

    /**
     * Renders Living Digital Glowing Eyes with multi-layer Radial Gradient.
     * Core luminous pupil/iris radiates outward onto the cyber visor screen.
     */
    private fun drawLivingEyeGlow(
        canvas: Canvas,
        mascotSize: Float,
        scaleX: Float,
        scaleY: Float,
        blinkSquashY: Float,
        isAmbientSleeping: Boolean
    ) {
        val pulse = 1.0f + 0.05f * sin(breathPhase * 1.5).toFloat()
        val eyeRadiusX = mascotSize * 0.038f * pulse
        val eyeRadiusY = mascotSize * 0.076f * pulse * blinkSquashY

        // Eye positions in mascot normalized coordinate space (centered at 0, 0)
        // Left eye center: (-0.09375 * mascotSize, -0.039 * mascotSize)
        // Right eye center: (+0.09375 * mascotSize, -0.039 * mascotSize)
        val eyeOffsetX = mascotSize * 0.09375f
        val eyeOffsetY = -mascotSize * 0.039f

        // Core iris color & bloom based on current state
        val (coreColor, glowColor) = when {
            isAmbientSleeping -> 0x5500E5FF.toInt() to 0x2200E5FF.toInt()
            currentState == NexoState.LISTENING -> 0xCC00E676.toInt() to 0x5500E676.toInt()
            currentState == NexoState.THINKING -> 0xCCFFB300.toInt() to 0x55FFB300.toInt()
            currentState == NexoState.SPEAKING -> 0xEE00E5FF.toInt() to 0x662979FF.toInt()
            currentState == NexoState.ERROR -> 0xCCFF5252.toInt() to 0x55FF5252.toInt()
            else -> 0xCC00E5FF.toInt() to 0x4400E5FF.toInt() // IDLE
        }

        canvas.save()
        canvas.concat(animMatrix)

        // Center on mascot surface
        val leftEyeCx = mascotSize / 2f - eyeOffsetX
        val rightEyeCx = mascotSize / 2f + eyeOffsetX
        val eyeCy = mascotSize / 2f + eyeOffsetY

        // Left Eye Pupil/Iris Radial Gradient Glow
        eyeRectLeft.set(leftEyeCx - eyeRadiusX, eyeCy - eyeRadiusY, leftEyeCx + eyeRadiusX, eyeCy + eyeRadiusY)
        eyeGlowPaint.shader = RadialGradient(
            leftEyeCx, eyeCy, eyeRadiusY.coerceAtLeast(8f),
            intArrayOf(coreColor, glowColor, 0x00000000),
            floatArrayOf(0.15f, 0.65f, 1.0f),
            Shader.TileMode.CLAMP
        )
        canvas.drawOval(eyeRectLeft, eyeGlowPaint)

        // Right Eye Pupil/Iris Radial Gradient Glow
        eyeRectRight.set(rightEyeCx - eyeRadiusX, eyeCy - eyeRadiusY, rightEyeCx + eyeRadiusX, eyeCy + eyeRadiusY)
        eyeGlowPaint.shader = RadialGradient(
            rightEyeCx, eyeCy, eyeRadiusY.coerceAtLeast(8f),
            intArrayOf(coreColor, glowColor, 0x00000000),
            floatArrayOf(0.15f, 0.65f, 1.0f),
            Shader.TileMode.CLAMP
        )
        canvas.drawOval(eyeRectRight, eyeGlowPaint)

        canvas.restore()
    }

    private fun drawAmbientAura(
        canvas: Canvas,
        cx: Float,
        cy: Float,
        radius: Float,
        isAmbientSleeping: Boolean
    ) {
        val auraColor = if (isAmbientSleeping) {
            0x15162B3A.toInt()
        } else {
            when (currentState) {
                NexoState.IDLE -> 0x33254E98.toInt()
                NexoState.LISTENING -> 0x4400E5FF.toInt()
                NexoState.THINKING -> 0x44FFB300.toInt()
                NexoState.SPEAKING -> 0x5500E5FF.toInt()
                NexoState.ERROR -> 0x44FF5252.toInt()
                NexoState.DISCONNECTED -> 0x22333333.toInt()
            }
        }

        val dimAuraColor = if (isAmbientSleeping) {
            auraColor
        } else {
            val a = ((auraColor ushr 24) * ambientDimMultiplier).toInt()
            (a shl 24) or (auraColor and 0x00FFFFFF)
        }

        auraPaint.shader = RadialGradient(
            cx, cy, radius * 1.3f,
            intArrayOf(dimAuraColor, 0x00000000),
            floatArrayOf(0.2f, 1.0f),
            Shader.TileMode.CLAMP
        )
        canvas.drawCircle(cx, cy, radius * 1.3f, auraPaint)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.action == MotionEvent.ACTION_DOWN) {
            performClick()
            idleStartTime = SystemClock.uptimeMillis() // Wake up from ambient sleep
            if (!voiceEnabled || SystemClock.uptimeMillis() < voiceReactionUntil) return true
            // Playful tactile spring squash & random happy expression
            stateExpression = listOf(
                MascotExpression.JAHIL,
                MascotExpression.SENANG,
                MascotExpression.KEREN,
                MascotExpression.SANGAT_SENANG
            ).random()
            updateExpression(SystemClock.uptimeMillis())
            return true
        }
        return super.onTouchEvent(event)
    }

    override fun performClick(): Boolean {
        super.performClick()
        return true
    }

    /**
     * Damped Harmonic Oscillator / Spring Physics Interpolator.
     * Provides smooth organic overshoot and settle when switching expressions.
     */
    private fun evaluateSpringProgress(t: Float): Float {
        val dampingRatio = 0.78f
        val angularFreq = 14f
        val dampedFreq = angularFreq * sqrt(1f - dampingRatio * dampingRatio)
        val envelope = exp(-dampingRatio * angularFreq * t)
        val osc = cos(dampedFreq * t) + (dampingRatio / sqrt(1f - dampingRatio * dampingRatio)) * sin(dampedFreq * t)
        return (1f - envelope * osc).coerceIn(0f, 1.0f)
    }
}

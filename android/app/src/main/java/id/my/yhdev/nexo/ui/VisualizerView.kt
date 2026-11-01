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
import kotlin.math.pow
import kotlin.math.sin
import kotlin.random.Random

/**
 * Living Mascot View with Smooth Physics-based Transitions.
 * Features 16 distinct expressions, cross-dissolve morphing,
 * anticipation squash & stretch, ambient breathing, natural blinking,
 * head-tilt micro-saccades, and audio-reactive vocal bouncing.
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

    // Expression States
    private var currentExpression: MascotExpression = MascotExpression.NETRAL
    private var previousExpression: MascotExpression = MascotExpression.NETRAL
    private var isTransitioning = false
    private var transitionStartTime = 0L
    private val transitionDuration = 280f // ms smooth morph

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

    // Transformation Rects & Matrix
    private val drawRect = RectF()
    private val shadowRect = RectF()
    private val animMatrix = Matrix()

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

    // Natural Blink Timer
    private var nextBlinkTime = SystemClock.uptimeMillis() + 3000L
    private var isBlinking = false
    private var blinkStartTime = 0L

    // Idle sleepiness timer
    private var idleStartTime = SystemClock.uptimeMillis()

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

        // Continuous Organic Breathing Oscillation
        breathPhase += dt * 2.2f
        if (breathPhase > Math.PI * 2) breathPhase -= (Math.PI * 2).toFloat()
        val reactionBob = when {
            !voiceEnabled -> sin(now / 150.0).toFloat() * 2f
            now < voiceReactionUntil -> -kotlin.math.abs(sin(now / 180.0)).toFloat() * 8f
            else -> 0f
        }
        val breathFloatY = sin(breathPhase.toDouble()).toFloat() * 5f + reactionBob
        val breathScale = 1.0f + (sin(breathPhase.toDouble()).toFloat() * 0.012f)

        // Random Saccade Glances & Head Tilts during IDLE
        if (currentState == NexoState.IDLE && now >= nextGlanceTime) {
            gazeTargetX = Random.nextInt(-14, 15).toFloat()
            gazeTargetY = Random.nextInt(-6, 7).toFloat()
            tiltTargetDeg = Random.nextFloat() * 4f - 2f
            nextGlanceTime = now + Random.nextLong(3000L, 6500L)
        }

        // Automatic Natural Blinking during IDLE & SPEAKING
        if (!isBlinking && now >= nextBlinkTime && currentState != NexoState.LISTENING) {
            isBlinking = true
            blinkStartTime = now
        }

        var blinkSquashY = 1.0f
        if (isBlinking) {
            val blinkElapsed = (now - blinkStartTime).toFloat()
            val blinkDuration = 150f
            if (blinkElapsed < blinkDuration) {
                val progress = blinkElapsed / blinkDuration
                // Smooth dip and bounce curve for eye blink
                blinkSquashY = (4f * (progress - 0.5f) * (progress - 0.5f)).coerceIn(0.12f, 1.0f)
            } else {
                isBlinking = false
                nextBlinkTime = now + Random.nextLong(3500L, 6000L)
            }
        }

        // Smooth Gaze & Tilt interpolation
        gazeCurrentX += (gazeTargetX - gazeCurrentX) * (dt * 5f)
        gazeCurrentY += (gazeTargetY - gazeCurrentY) * (dt * 5f)
        tiltCurrentDeg += (tiltTargetDeg - tiltCurrentDeg) * (dt * 4f)

        // Transition Progress & Squash/Stretch Physics
        var transProgress = 1.0f
        var squashStretchX = 1.0f
        var squashStretchY = 1.0f

        if (isTransitioning) {
            val elapsed = (now - transitionStartTime).toFloat()
            if (elapsed < transitionDuration) {
                val t = elapsed / transitionDuration
                transProgress = easeInOutCubic(t)

                // Anticipation Squash & Stretch Curve:
                // Phase 1 (0..0.4): Dip down (squash)
                // Phase 2 (0.4..0.8): Pop up into new emotion (stretch)
                // Phase 3 (0.8..1.0): Settle
                if (t < 0.4f) {
                    val p = t / 0.4f
                    val dip = sin(p * Math.PI).toFloat() * 0.08f
                    squashStretchY -= dip
                    squashStretchX += dip * 0.7f
                } else if (t < 0.8f) {
                    val p = (t - 0.4f) / 0.4f
                    val pop = sin(p * Math.PI).toFloat() * 0.09f
                    squashStretchY += pop
                    squashStretchX -= pop * 0.6f
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

        // Calculate Final Geometry
        val viewW = width.toFloat()
        val viewH = height.toFloat()
        val cx = viewW / 2f + gazeCurrentX
        val cy = viewH / 2f + gazeCurrentY + breathFloatY + speechBounceY

        val mascotSize = minOf(viewW, viewH) * 0.82f
        val finalScaleX = breathScale * squashStretchX * speechScale * listenScale
        val finalScaleY = breathScale * squashStretchY * speechScale * listenScale * blinkSquashY

        // Draw 3D Perspective Floor Shadow under character
        val floorY = cy + (mascotSize * 0.44f)
        val shadowW = mascotSize * 0.38f * (1f / finalScaleY.coerceAtLeast(0.5f))
        val shadowH = mascotSize * 0.08f * (1f / finalScaleY.coerceAtLeast(0.5f))
        shadowRect.set(cx - shadowW, floorY - shadowH, cx + shadowW, floorY + shadowH)
        shadowPaint.shader = RadialGradient(
            cx, floorY, shadowW.coerceAtLeast(10f),
            intArrayOf(0x66000000, 0x00000000),
            floatArrayOf(0.15f, 1.0f),
            Shader.TileMode.CLAMP
        )
        canvas.drawOval(shadowRect, shadowPaint)

        // Draw Ambient Lighting Aura behind mascot
        drawAmbientAura(canvas, cx, cy, mascotSize * 0.65f)

        // Setup Matrix for rotation, scale, and translation
        animMatrix.reset()
        animMatrix.postTranslate(-mascotSize / 2f, -mascotSize / 2f)
        animMatrix.postScale(finalScaleX, finalScaleY)
        animMatrix.postRotate(tiltCurrentDeg)
        animMatrix.postTranslate(cx, cy)

        val prevBmp = bitmapCache[previousExpression]
        val currBmp = bitmapCache[currentExpression]

        if (isTransitioning && prevBmp != null && currBmp != null) {
            // Smooth Cross-Dissolve Alpha Blending
            val prevAlpha = ((1f - transProgress) * 255).toInt().coerceIn(0, 255)
            val currAlpha = (transProgress * 255).toInt().coerceIn(0, 255)

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
                srcPaint.alpha = 255
                canvas.save()
                canvas.concat(animMatrix)
                drawRect.set(0f, 0f, mascotSize, mascotSize)
                canvas.drawBitmap(bmpToDraw, null, drawRect, srcPaint)
                canvas.restore()
            }
        }

        // Continuous 60 FPS Frame Loop
        postInvalidateOnAnimation()
    }

    private fun drawAmbientAura(canvas: Canvas, cx: Float, cy: Float, radius: Float) {
        val auraColor = when (currentState) {
            NexoState.IDLE -> 0x33254E98.toInt()
            NexoState.LISTENING -> 0x4400E5FF.toInt()
            NexoState.THINKING -> 0x44FFB300.toInt()
            NexoState.SPEAKING -> 0x5500E5FF.toInt()
            NexoState.ERROR -> 0x44FF5252.toInt()
            NexoState.DISCONNECTED -> 0x22333333.toInt()
        }

        auraPaint.shader = RadialGradient(
            cx, cy, radius * 1.3f,
            intArrayOf(auraColor, 0x00000000),
            floatArrayOf(0.2f, 1.0f),
            Shader.TileMode.CLAMP
        )
        canvas.drawCircle(cx, cy, radius * 1.3f, auraPaint)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        if (event.action == MotionEvent.ACTION_DOWN) {
            performClick()
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

    private fun easeInOutCubic(x: Float): Float {
        return if (x < 0.5f) {
            4f * x * x * x
        } else {
            1f - (-2f * x + 2f).toDouble().pow(3.0).toFloat() / 2f
        }
    }
}

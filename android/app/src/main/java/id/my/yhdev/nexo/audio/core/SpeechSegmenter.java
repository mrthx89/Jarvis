package id.my.yhdev.nexo.audio.core;

/** Uncalibrated boundaries for 512-sample speech probabilities, never identity. */
public final class SpeechSegmenter {
    public static final int NONE = 0, START = 1, END = 2;
    private int onset;
    private int silence;
    private boolean active;
    public int boundary(float probability) {
        if (!Float.isFinite(probability) || probability < 0 || probability > 1)
            throw new IllegalArgumentException("vad_probability");
        if (!active) {
            onset = probability >= .60f ? onset + 1 : 0;
            if (onset == 3) { active = true; silence = 0; return START; }
        } else {
            silence = probability < .35f ? silence + 1 : 0;
            if (silence >= 32) { reset(); return END; }
        }
        return NONE;
    }
    public void reset() { onset = 0; silence = 0; active = false; }
}

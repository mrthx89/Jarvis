package id.my.yhdev.nexo.audio.core;

import java.util.Arrays;
import java.util.concurrent.atomic.AtomicLong;

/** Capture consumer worker plus generation-fenced control methods; no socket dependency. */
public final class LocalCapture {
    public interface Vad { float probability(float[] window); }
    private final AtomicLong generation = new AtomicLong();
    private final UtteranceBuffer buffer = new UtteranceBuffer();
    private final SpeechSegmenter segmenter = new SpeechSegmenter();
    private final float[] window = new float[512];
    private final byte[] pair = new byte[2];
    private int windowSize;
    private int low = -1;
    private boolean automatic;
    private Vad vad;
    private String purpose;
    private String state = "PAUSED";
    private String error;

    /** Call only after the previous CapturePump has joined. Does not start hardware. */
    public synchronized long arm(String requestedPurpose, boolean auto, Vad model) {
        if (!state.equals("PAUSED") && !state.equals("ERROR")) throw new IllegalStateException("capture_busy");
        if (auto && model == null) throw new IllegalStateException("auto_unavailable");
        buffer.cancel(); segmenter.reset(); windowSize = 0; low = -1;
        Arrays.fill(window, 0); automatic = auto; vad = model; purpose = requestedPurpose;
        error = null; state = "ARMED"; return generation.incrementAndGet();
    }
    public synchronized boolean start(long token, long now) {
        if (token != generation.get() || !state.equals("ARMED") || automatic) return false;
        if (!buffer.start(purpose, now)) return false;
        state = "CAPTURING"; return true;
    }
    public void accept(long token, byte[] data, int count, long now) {
        if (token != generation.get()) return;
        if (count <= 0 || count > data.length) { cancel("invalid_read"); return; }
        if (!automatic) {
            synchronized (this) {
                if (token != generation.get()) return;
                buffer.accept(data, count, now); updateState();
            }
            return;
        }
        for (int i = 0; i < count; i++) {
            boolean infer = false;
            synchronized (this) {
                if (token != generation.get() || (!state.equals("ARMED") && !state.equals("CAPTURING"))) return;
                if (low < 0) { low = data[i] & 255; continue; }
                pair[0] = (byte) low; pair[1] = data[i];
                int sample = (short) (low | (data[i] << 8)); low = -1;
                buffer.accept(pair, 2, now); updateState();
                if (state.equals("SEALED") || state.equals("ERROR")) return;
                window[windowSize++] = sample / 32768f;
                infer = windowSize == 512;
            }
            if (infer) {
                float probability;
                try { probability = vad.probability(window); }
                catch (RuntimeException e) { if (token == generation.get()) cancel("auto_unavailable"); return; }
                synchronized (this) {
                    if (token != generation.get()) return;
                    windowSize = 0;
                    int boundary;
                    try { boundary = segmenter.boundary(probability); }
                    catch (IllegalArgumentException e) { cancel("auto_unavailable"); return; }
                    if (boundary == SpeechSegmenter.START && state.equals("ARMED")) {
                        if (buffer.start(purpose, now)) state = "CAPTURING";
                    } else if (boundary == SpeechSegmenter.END && state.equals("CAPTURING")) {
                        buffer.finish("vad_end", now); updateState();
                    }
                }
            }
        }
    }
    private void updateState() {
        if (buffer.error() != null) { error = buffer.error(); state = "ERROR"; }
        else if (buffer.sealed() != null) state = "SEALED";
    }
    public synchronized UtteranceBuffer.Sealed finish(long token, long now) {
        if (token != generation.get() || !state.equals("CAPTURING")) return null;
        if (low >= 0) { cancel("unaligned_pcm"); return null; }
        UtteranceBuffer.Sealed sealed = buffer.finish("manual_finish", now); updateState(); return sealed;
    }
    public void cancel(String reason) {
        generation.incrementAndGet();
        synchronized (this) {
            buffer.cancel(); error = reason; state = "PAUSED";
        }
    }
    public synchronized void fail(String reason) { cancel(reason); state = "ERROR"; }
    public synchronized void release(UtteranceBuffer.Sealed sealed) {
        if (sealed == null || buffer.sealed() != sealed) return;
        buffer.release(sealed); state = "PAUSED"; generation.incrementAndGet();
    }
    public synchronized UtteranceBuffer.Sealed sealed() { return buffer.sealed(); }
    public synchronized String state() { return state; }
    public synchronized String error() { return error; }
    public UtteranceBuffer metrics() { return buffer; }
}

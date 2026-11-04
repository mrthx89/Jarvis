package id.my.yhdev.nexo.audio.core;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.UUID;

/** Worker-owned PCM; sealed storage cannot be reused until its consumers release it. */
public final class UtteranceBuffer {
    public static final int MAX_SAMPLES = 320000;
    public static final int PRE_ROLL_SAMPLES = 6400;
    public static final class Sealed {
        private final byte[] pcm;
        private final int bytes;
        private boolean released;
        public final String id = UUID.randomUUID().toString();
        public final String purpose;
        public final String endReason;
        public final long sealedAt;
        private String digest;
        private Sealed(byte[] pcm, int bytes, String purpose, String reason, long now) {
            this.pcm = pcm; this.bytes = bytes; this.purpose = purpose;
            this.endReason = reason; this.sealedAt = now;
        }
        public int samples() { return bytes / 2; }
        public String status(String message) {
            return endReason.equals("duration_limit") ?
                message + " • Rekaman selesai: batas 20 detik tercapai (duration_limit)." : message;
        }
        public synchronized String sha256() {
            checkLive();
            if (digest == null) {
                try {
                    MessageDigest hash = MessageDigest.getInstance("SHA-256");
                    hash.update(pcm, 0, bytes);
                    StringBuilder hex = new StringBuilder(64);
                    for (byte b : hash.digest()) hex.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
                    digest = hex.toString();
                } catch (NoSuchAlgorithmException e) { throw new AssertionError(e); }
            }
            return digest;
        }
        public synchronized byte[] chunk(int sampleOffset, int maxBytes) {
            checkLive();
            if (sampleOffset < 0 || sampleOffset >= samples() || maxBytes < 2 || (maxBytes & 1) != 0)
                throw new IllegalArgumentException("invalid_length");
            int offset = sampleOffset * 2;
            return Arrays.copyOfRange(pcm, offset, Math.min(bytes, offset + maxBytes));
        }
        public synchronized byte[] copyForTest() { checkLive(); return Arrays.copyOf(pcm, bytes); }
        public synchronized boolean isReleased() { return released; }
        private void checkLive() { if (released) throw new IllegalStateException("expired"); }
        public synchronized void erase() { Arrays.fill(pcm, (byte) 0); digest = null; released = true; }
    }
    private final byte[] pcm;
    private final byte[] pre;
    private int preWrite;
    private int preSize;
    private int size;
    private int carry = -1;
    private boolean capturing;
    private String purpose;
    private String failure;
    private Sealed sealed;
    private long overwrittenSamples;
    private long samples;
    private long clipped;
    private double squares;
    private int peak;

    public UtteranceBuffer() { this(MAX_SAMPLES, PRE_ROLL_SAMPLES); }
    public UtteranceBuffer(int maxSamples, int preRollSamples) {
        if (maxSamples < 1 || maxSamples > MAX_SAMPLES || preRollSamples < 0 || preRollSamples >= maxSamples)
            throw new IllegalArgumentException("pcm_budget");
        pcm = new byte[maxSamples * 2]; pre = new byte[preRollSamples * 2];
    }
    public synchronized void accept(byte[] data, int count) { accept(data, count, 0); }
    public synchronized void accept(byte[] data, int count, long now) {
        if (count < 1 || count > data.length) { invalidate("invalid_read"); return; }
        if (failure != null || sealed != null) return;
        if (!capturing && samples >= 8000) {
            squares *= 0.5;
            samples /= 2;
        }
        for (int i = 0; i < count; i++) {
            if (carry < 0) { carry = data[i] & 255; continue; }
            byte low = (byte) carry, high = data[i]; carry = -1;
            int value = (short) ((low & 255) | (high << 8));
            samples++; squares += (double) value * value; peak = Math.max(peak, Math.abs(value));
            if (value == Short.MIN_VALUE || value == Short.MAX_VALUE) clipped++;
            if (capturing) {
                pcm[size++] = low; pcm[size++] = high;
                if (size == pcm.length) { finish("duration_limit", now); return; }
            } else if (pre.length > 0) {
                if (preSize == pre.length) overwrittenSamples++; else preSize += 2;
                pre[preWrite] = low; pre[(preWrite + 1) % pre.length] = high;
                preWrite = (preWrite + 2) % pre.length;
            }
        }
    }
    public synchronized boolean start(String requestedPurpose, long now) {
        if (capturing || sealed != null || failure != null) return false;
        if (!requestedPurpose.equals("conversation") && !requestedPurpose.equals("enrollment") && !requestedPurpose.equals("diagnostic"))
            throw new IllegalArgumentException("purpose");
        purpose = requestedPurpose; size = 0;
        samples = 0; clipped = 0; squares = 0; peak = 0;
        int oldest = pre.length == 0 ? 0 : (preWrite - preSize + pre.length) % pre.length;
        for (int i = 0; i < preSize; i++) pcm[size++] = pre[(oldest + i) % pre.length];
        for (int i = 0; i + 1 < size; i += 2) {
            int value = (short) ((pcm[i] & 255) | (pcm[i + 1] << 8));
            samples++; squares += (double) value * value; peak = Math.max(peak, Math.abs(value));
            if (value == Short.MIN_VALUE || value == Short.MAX_VALUE) clipped++;
        }
        Arrays.fill(pre, (byte) 0); preSize = 0; preWrite = 0;
        capturing = true; return true;
    }
    public synchronized Sealed finish(String reason, long now) {
        if (!capturing) return null;
        if (carry >= 0) { invalidate("unaligned_pcm"); return null; }
        if (size == 0) { invalidate("no_audio"); return null; }
        if (!reason.equals("manual_finish") && !reason.equals("vad_end") && !reason.equals("duration_limit"))
            throw new IllegalArgumentException("end_reason");
        capturing = false;
        sealed = new Sealed(pcm, size, purpose, reason, now);
        return sealed;
    }
    public synchronized void invalidate(String code) { cancel(); failure = code; }
    public synchronized Sealed sealed() { return sealed; }
    public synchronized boolean isCapturing() { return capturing; }
    public synchronized String error() { return failure; }
    public synchronized void release(Sealed item) {
        if (sealed == item && item != null) { item.erase(); sealed = null; size = 0; carry = -1; }
    }
    public synchronized void cancel() {
        if (sealed != null) sealed.erase();
        sealed = null; capturing = false; size = 0; carry = -1; preSize = 0; preWrite = 0;
        samples = 0; clipped = 0; squares = 0; peak = 0; overwrittenSamples = 0;
        Arrays.fill(pcm, (byte) 0); Arrays.fill(pre, (byte) 0); failure = null;
    }
    public synchronized long overwrittenSamples() { return overwrittenSamples; }
    public synchronized long sampleCount() { return samples; }
    public synchronized long clippedSamples() { return clipped; }
    public synchronized int peak() { return peak; }
    public synchronized double rms() { return samples == 0 ? 0 : Math.sqrt(squares / samples) / 32768.0; }
}

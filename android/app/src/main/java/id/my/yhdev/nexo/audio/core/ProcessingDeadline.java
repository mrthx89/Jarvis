package id.my.yhdev.nexo.audio.core;

/** Service-owned terminal-result policy, independent of PCM retry/cache lifetime. */
public final class ProcessingDeadline {
    public static final long TIMEOUT_MS = 30000;
    private String pending;
    private long acceptedAt;
    private boolean armed;

    public synchronized void begin(String utterance) { clear(); pending = utterance; }
    public synchronized boolean accepted(String utterance, long now) {
        if (pending == null || !pending.equals(utterance)) return false;
        if (!armed) { acceptedAt = now; armed = true; }
        return true;
    }
    /** Refreshes the inactivity window while fenced server output/audio is actively arriving. */
    public synchronized void activity(long now) {
        if (armed && now >= acceptedAt) acceptedAt = now;
    }
    public synchronized boolean complete(String utterance, long now) {
        if (!armed || pending == null || !pending.equals(utterance) || expired(now)) return false;
        clear();
        return true;
    }
    public synchronized boolean expired(long now) {
        return armed && (now < acceptedAt || now - acceptedAt >= TIMEOUT_MS);
    }
    public synchronized void clear() { pending = null; armed = false; acceptedAt = 0; }
}

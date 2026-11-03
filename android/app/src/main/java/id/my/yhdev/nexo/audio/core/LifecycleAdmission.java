package id.my.yhdev.nexo.audio.core;

import java.util.concurrent.atomic.AtomicLong;

/** Monotonic callback admission; no recorder, consumer or hardware monitor is acquired. */
public final class LifecycleAdmission {
    private final AtomicLong value = new AtomicLong();
    public long generation() { return value.get(); }
    public long advance() {
        while (true) {
            long prior = value.get();
            if (prior < 0) return -1;
            if (value.compareAndSet(prior, prior + 1)) return prior + 1;
        }
    }
    /** Only the current callback can revoke; the returned token fences queued failure handling. */
    public long revoke(long expected) {
        if (expected < 0) return -1;
        return value.compareAndSet(expected, expected + 1) ? expected + 1 : -1;
    }
    public void close() { value.set(-1); }
}

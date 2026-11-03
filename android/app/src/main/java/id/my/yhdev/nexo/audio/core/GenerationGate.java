package id.my.yhdev.nexo.audio.core;

import java.util.concurrent.atomic.AtomicLong;

/** Atomic startup publication cannot resurrect a route-invalidated capture. */
public final class GenerationGate {
    private final AtomicLong value = new AtomicLong();
    public long begin() { return value.updateAndGet(v -> (Math.abs(v) + 1)); }
    public boolean activate(long token) { return token > 0 && value.compareAndSet(token, -token); }
    public void invalidate(long token) {
        value.compareAndSet(token, token + 1);
        value.compareAndSet(-token, token + 1);
    }
    public boolean isActive(long token) { return value.get() == -token; }
    public boolean matches(long token) { return Math.abs(value.get()) == token; }
}

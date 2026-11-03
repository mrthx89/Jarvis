package id.my.yhdev.nexo.audio.core;

import java.util.Arrays;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.locks.LockSupport;
import java.util.function.Consumer;

/** No consumer, network or DSP lock is ever acquired by the read thread. */
public final class CapturePump {
    public interface Source { int read(byte[] target) throws Exception; void stop(); void release(); }
    public interface Sink { void accept(byte[] data, int count); }
    private final Source source;
    private final Sink sink;
    private final Consumer<String> onError;
    private final PcmHandoff handoff;
    private final byte[] readBuffer;
    private final byte[] workerBuffer;
    private final int maxPending;
    private final AtomicBoolean running = new AtomicBoolean();
    private final AtomicBoolean stopped = new AtomicBoolean();
    private final AtomicBoolean released = new AtomicBoolean();
    private volatile boolean readerDone;
    private volatile boolean finishing;
    private Thread reader;
    private Thread worker;
    private Thread monitor;
    private volatile long producedBytes;
    private volatile long consumedBytes;
    private final AtomicBoolean errorReported = new AtomicBoolean();
    private boolean started;

    public CapturePump(Source source, Sink sink, Consumer<String> error, int slots, int bytes, int maxPending) {
        this.source = source; this.sink = sink; this.onError = error;
        handoff = new PcmHandoff(slots, bytes); readBuffer = new byte[bytes]; workerBuffer = new byte[bytes];
        this.maxPending = maxPending;
    }
    public synchronized void start() {
        if (started) throw new IllegalStateException("session_already_started");
        started = true; running.set(true);
        reader = new Thread(() -> {
            try {
                while (running.get() && !finishing) {
                    int count = source.read(readBuffer);
                    if (!running.get()) break;
                    if (count <= 0) {
                        if (!finishing) handoff.fail(count == -6 ? "recorder_dead" : count == -3 ? "recorder_invalid_operation" : "recorder_read_error");
                        break;
                    }
                    producedBytes += count;
                    if (producedBytes - consumedBytes > 2L * maxPending) { handoff.fail("vad_overrun"); break; }
                    if (!handoff.offer(readBuffer, count)) break;
                }
            } catch (SecurityException e) { if (running.get()) handoff.fail("permission_loss"); }
            catch (Exception e) { if (running.get()) handoff.fail("recorder_read_error"); }
            finally { readerDone = true; }
        }, "Nexo-PcmRead");
        worker = new Thread(() -> {
            try {
                while (running.get()) {
                    if (handoff.error() != null) { reportError(); break; }
                    int count = handoff.poll(workerBuffer);
                    if (count > 0) {
                        if (running.get()) sink.accept(workerBuffer, count);
                        consumedBytes += count;
                    } else if (readerDone) break;
                    else LockSupport.parkNanos(1_000_000);
                }
            } catch (Exception e) { handoff.fail("consumer_error"); onError.accept("consumer_error"); }
            finally {
                running.set(false); stopSource();
                try { if (reader != Thread.currentThread()) reader.join(1000); }
                catch (InterruptedException e) { Thread.currentThread().interrupt(); }
                if (!reader.isAlive()) { releaseSource(); handoff.clearAfterJoin(); Arrays.fill(readBuffer, (byte) 0); }
                Arrays.fill(workerBuffer, (byte) 0);
            }
        }, "Nexo-PcmWorker");
        worker.setDaemon(true); reader.setDaemon(true);
        monitor = new Thread(() -> {
            while (running.get()) {
                if (handoff.error() != null) { reportError(); running.set(false); stopSource(); break; }
                LockSupport.parkNanos(1_000_000);
            }
        }, "Nexo-CaptureWatchdog");
        monitor.setDaemon(true);
        reader.start(); worker.start(); monitor.start();
    }
    private void reportError() {
        if (errorReported.compareAndSet(false, true)) onError.accept(handoff.error());
    }
    private void stopSource() { if (stopped.compareAndSet(false, true)) source.stop(); }
    private void releaseSource() { if (released.compareAndSet(false, true)) source.release(); }
    public boolean finish(long timeoutMs) {
        finishing = true; stopSource();
        try {
            if (worker != null && worker != Thread.currentThread()) worker.join(Math.max(1, timeoutMs));
        } catch (InterruptedException e) { Thread.currentThread().interrupt(); return false; }
        return (reader == null || !reader.isAlive()) && (worker == null || !worker.isAlive());
    }
    public boolean stop(long timeoutMs) {
        running.set(false); stopSource();
        long end = System.nanoTime() + timeoutMs * 1_000_000L;
        try {
            if (reader != null && reader != Thread.currentThread()) reader.join(Math.max(1, timeoutMs));
            if (worker != null && worker != Thread.currentThread())
                worker.join(Math.max(1, (end - System.nanoTime()) / 1_000_000L));
        } catch (InterruptedException e) { Thread.currentThread().interrupt(); return false; }
        boolean done = (reader == null || !reader.isAlive()) && (worker == null || !worker.isAlive());
        if (done) { releaseSource(); handoff.clearAfterJoin(); }
        return done;
    }
    public boolean isRunning() { return running.get(); }
    public int highWater() { return handoff.highWater(); }
}

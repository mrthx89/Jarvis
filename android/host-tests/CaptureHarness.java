import id.my.yhdev.nexo.audio.core.CapturePump;
import java.util.concurrent.*;
import java.util.Arrays;
import java.util.concurrent.atomic.*;

public final class CaptureHarness {
    private CaptureHarness() {}
    public static void main(String[] args) throws Exception {
        CountDownLatch readEntered = new CountDownLatch(1);
        CountDownLatch unblockRead = new CountDownLatch(1);
        CountDownLatch callbackEntered = new CountDownLatch(1);
        CountDownLatch unblockCallback = new CountDownLatch(1);
        CountDownLatch stopCalled = new CountDownLatch(1);
        AtomicInteger reads = new AtomicInteger();
        AtomicInteger stopped = new AtomicInteger();
        AtomicInteger released = new AtomicInteger();
        AtomicInteger callbacks = new AtomicInteger();
        AtomicReference<String> failure = new AtomicReference<>();
        CapturePump.Source source = new CapturePump.Source() {
            public int read(byte[] target) throws InterruptedException {
                int n = reads.incrementAndGet();
                if (n <= 2) { target[0] = (byte) n; return 1; }
                readEntered.countDown(); unblockRead.await(); target[0] = 99; return 1;
            }
            public void stop() { stopped.incrementAndGet(); stopCalled.countDown(); unblockRead.countDown(); }
            public void release() { released.incrementAndGet(); }
        };
        CapturePump pump = new CapturePump(source, (data, count) -> {
            callbacks.incrementAndGet();
            callbackEntered.countDown();
            try { unblockCallback.await(); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        }, failure::set, 32, 3200, 32);
        pump.start();
        assert callbackEntered.await(2, TimeUnit.SECONDS);
        assert readEntered.await(2, TimeUnit.SECONDS); // Consumer lock never stops the reader.
        AtomicBoolean joined = new AtomicBoolean();
        Thread stop = new Thread(() -> joined.set(pump.stop(2000)), "test-stop");
        stop.start();
        assert stopCalled.await(2, TimeUnit.SECONDS);
        unblockCallback.countDown(); stop.join(3000);
        assert !stop.isAlive() && joined.get();
        assert stopped.get() == 1 && released.get() == 1;
        assert callbacks.get() == 1; // Queued and blocked-read tails are fenced on STOP.
        assert pump.stop(100) && released.get() == 1;
        assert failure.get() == null;
        CountDownLatch lastRead = new CountDownLatch(1);
        CountDownLatch stopRead = new CountDownLatch(1);
        AtomicInteger next = new AtomicInteger();
        AtomicInteger sum = new AtomicInteger();
        CapturePump graceful = new CapturePump(new CapturePump.Source() {
            public int read(byte[] target) throws InterruptedException {
                int n = next.incrementAndGet();
                if (n < 4) { target[0] = (byte) n; return 1; }
                lastRead.countDown(); stopRead.await(); target[0] = 4; return 1;
            }
            public void stop() { stopRead.countDown(); }
            public void release() {}
        }, (data, count) -> sum.addAndGet(data[0]), code -> { throw new AssertionError(code); }, 32, 3200, 32);
        graceful.start();
        assert lastRead.await(2, TimeUnit.SECONDS);
        assert graceful.finish(2000);
        assert sum.get() == 10; // Finish drains captured slots; cancel intentionally does not.
        CountDownLatch slowSink = new CountDownLatch(1);
        CountDownLatch sinkBusy = new CountDownLatch(1);
        CountDownLatch aborted = new CountDownLatch(1);
        CountDownLatch allowBurst = new CountDownLatch(1);
        AtomicInteger burst = new AtomicInteger();
        AtomicReference<String> lagError = new AtomicReference<>();
        CapturePump lag = new CapturePump(new CapturePump.Source() {
            public int read(byte[] target) throws InterruptedException {
                int n = burst.incrementAndGet();
                if (n > 1) { sinkBusy.await(); allowBurst.await(); }
                Arrays.fill(target, (byte) 1); return target.length;
            }
            public void stop() { aborted.countDown(); }
            public void release() {}
        }, (data, count) -> { sinkBusy.countDown(); try { slowSink.await(); }
            catch (InterruptedException e) { Thread.currentThread().interrupt(); } }, lagError::set, 32, 3200, 8192);
        lag.start();
        assert sinkBusy.await(2, TimeUnit.SECONDS);
        allowBurst.countDown();
        assert aborted.await(2, TimeUnit.SECONDS); // Hardware stops even if inference is stuck.
        assert "vad_overrun".equals(lagError.get());
        slowSink.countDown(); assert lag.stop(2000);
        for (int failureCode : new int[]{-6, -3, 0}) {
            AtomicReference<String> code = new AtomicReference<>();
            CountDownLatch reported = new CountDownLatch(1);
            CapturePump bad = new CapturePump(new CapturePump.Source() {
                public int read(byte[] target) { return failureCode; }
                public void stop() {}
                public void release() {}
            }, (data, count) -> { throw new AssertionError("invalid read delivered"); },
                reason -> { code.set(reason); reported.countDown(); }, 32, 3200, 8192);
            bad.start(); assert reported.await(2, TimeUnit.SECONDS); assert bad.stop(2000);
            assert code.get().equals(failureCode == -6 ? "recorder_dead" : failureCode == -3 ? "recorder_invalid_operation" : "recorder_read_error");
        }
        CountDownLatch permissionReported = new CountDownLatch(1);
        AtomicReference<String> permissionCode = new AtomicReference<>();
        CapturePump denied = new CapturePump(new CapturePump.Source() {
            public int read(byte[] target) { throw new SecurityException("fixture"); }
            public void stop() {}
            public void release() {}
        }, (data, count) -> { throw new AssertionError("denied read delivered"); },
            reason -> { permissionCode.set(reason); permissionReported.countDown(); }, 32, 3200, 8192);
        denied.start(); assert permissionReported.await(2, TimeUnit.SECONDS); assert denied.stop(2000);
        assert permissionCode.get().equals("permission_loss");
        System.out.println("PASS capture");
    }
}

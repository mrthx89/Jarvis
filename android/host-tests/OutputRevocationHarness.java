import id.my.yhdev.nexo.audio.core.AudioOwnership;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Host admission race: hardware is deliberately blocked, never real audio. */
public final class OutputRevocationHarness {
    private static final String C = "00000000-0000-4000-8000-000000000010";
    private static final String R = "00000000-0000-4000-8000-000000000012";
    private OutputRevocationHarness() { }
    public static void main(String[] args) throws Exception {
        AudioOwnership gate = new AudioOwnership();
        gate.foreground(true); gate.connected(C, 0); gate.resume(R, 0);
        gate.resumed(C, R, false, 1); gate.recorderReady(gate.generation(), true);
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        CountDownLatch revoked = new CountDownLatch(1);
        Runnable hardware = () -> {
            entered.countDown();
            try { if (!release.await(5, TimeUnit.SECONDS)) throw new AssertionError("hardware latch"); }
            catch (InterruptedException e) { throw new AssertionError(e); }
        };
        Thread output = new Thread(() -> gate.runOutput(C, 1L, hardware));
        output.start();
        assert entered.await(2, TimeUnit.SECONDS);
        Thread cancellation = new Thread(() -> { gate.disconnected(); revoked.countDown(); });
        cancellation.start();
        boolean immediate = revoked.await(500, TimeUnit.MILLISECONDS);
        release.countDown(); output.join(2000); cancellation.join(2000);
        assert immediate : "ownership revocation waited for blocked hardware";
        boolean[] started = {false};
        assert !gate.runOutput(C, 1L, () -> started[0] = true);
        assert !started[0] : "revoked output reached actual operation";
        System.out.println("PASS output_revocation");
    }
}

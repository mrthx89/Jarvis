import id.my.yhdev.nexo.audio.core.LifecycleAdmission;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

public final class LifecycleAdmissionHarness {
    private LifecycleAdmissionHarness() { }
    public static void main(String[] args) throws Exception {
        LifecycleAdmission gate = new LifecycleAdmission();
        long first = gate.advance();
        long replacement = gate.advance();
        assert gate.revoke(first) == -1 : "stale failure revoked replacement";
        assert gate.generation() == replacement;
        long failed = gate.revoke(replacement);
        assert failed > replacement : "failure did not immediately fence read/upload callbacks";
        assert gate.revoke(replacement) == -1 : "duplicate failure";
        CountDownLatch posted = new CountDownLatch(1);
        CountDownLatch run = new CountDownLatch(1);
        boolean[] touched = {false};
        Thread callback = new Thread(() -> {
            posted.countDown();
            try { if (!run.await(2, TimeUnit.SECONDS)) throw new AssertionError(); }
            catch (InterruptedException e) { throw new AssertionError(e); }
            if (gate.generation() == failed) touched[0] = true;
        });
        callback.start(); assert posted.await(2, TimeUnit.SECONDS);
        gate.advance(); run.countDown(); callback.join(2000);
        assert !touched[0] : "queued failure touched replacement";
        long beforeShutdown = gate.generation();
        gate.close();
        assert gate.revoke(beforeShutdown) == -1 : "callback submitted cleanup after shutdown";
        assert gate.advance() == -1 : "shutdown admitted new capture";
        System.out.println("PASS lifecycle_admission");
    }
}

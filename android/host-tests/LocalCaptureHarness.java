import id.my.yhdev.nexo.audio.core.LocalCapture;
import id.my.yhdev.nexo.audio.core.UtteranceBuffer;
import java.util.Arrays;

public final class LocalCaptureHarness {
    private LocalCaptureHarness() {}
    public static void main(String[] args) {
        LocalCapture capture = new LocalCapture();
        long first = capture.arm("enrollment", false, null);
        assert capture.start(first, 0);
        capture.accept(first, new byte[]{1,2,0,0}, 4, 1);
        UtteranceBuffer.Sealed pcm = capture.finish(first, 2);
        assert pcm != null && pcm.samples() == 2;
        assert capture.finish(first, 3) == null;
        capture.cancel("route_change");
        assert pcm.isReleased();
        long second = capture.arm("conversation", false, null);
        capture.accept(first, new byte[]{9,9}, 2, 4);
        assert !capture.start(first, 5);
        assert capture.start(second, 5);
        capture.accept(second, new byte[]{3,4}, 2, 6);
        pcm = capture.finish(second, 7);
        assert Arrays.equals(pcm.copyForTest(), new byte[]{3,4});
        capture.release(pcm);
        long next = capture.arm("conversation", false, null);
        assert capture.start(next, 8);
        capture.release(pcm);
        assert capture.state().equals("CAPTURING");
        capture.cancel("background");
        assert capture.state().equals("PAUSED");
        long auto = capture.arm("conversation", true, window -> .9f);
        for (int i = 0; i < 3; i++) capture.accept(auto, new byte[1024], 1024, 8 + i);
        assert capture.state().equals("CAPTURING");
        capture.cancel("stop");
        System.out.println("PASS local");
    }
}

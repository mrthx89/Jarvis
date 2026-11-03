import id.my.yhdev.nexo.audio.core.PcmHandoff;
import java.util.Arrays;
import id.my.yhdev.nexo.audio.core.UtteranceBuffer;
import id.my.yhdev.nexo.audio.core.SpeechSegmenter;

public final class AudioCoreHarness {
    private AudioCoreHarness() {}
    public static void main(String[] args) throws Exception {
        switch (args[0]) {
            case "handoff": handoff(); break;
            case "utterance": utterance(); break;
            case "vad": vad(); break;
            default: throw new AssertionError("unknown scenario");
        }
        System.out.println("PASS " + args[0]);
    }
    private static void vad() {
        SpeechSegmenter vad = new SpeechSegmenter();
        assert vad.boundary(.7f) == SpeechSegmenter.NONE;
        assert vad.boundary(.7f) == SpeechSegmenter.NONE;
        assert vad.boundary(.7f) == SpeechSegmenter.START;
        for (int i = 0; i < 10; i++) assert vad.boundary(.1f) == SpeechSegmenter.NONE;
        assert vad.boundary(.4f) == SpeechSegmenter.NONE;
        for (int i = 0; i < 31; i++) assert vad.boundary(.1f) == SpeechSegmenter.NONE;
        assert vad.boundary(.1f) == SpeechSegmenter.END;
        vad.reset();
        for (int i = 0; i < 200; i++) assert vad.boundary(.2f) == SpeechSegmenter.NONE;
    }
    private static void utterance() {
        UtteranceBuffer u = new UtteranceBuffer(6, 2);
        u.accept(new byte[]{1, 2, 3}, 3);
        u.accept(new byte[]{4, 5, 6}, 3);
        assert u.start("conversation", 10);
        u.accept(new byte[]{0, 0, 7}, 3);
        u.accept(new byte[]{8}, 1);
        UtteranceBuffer.Sealed s = u.finish("manual_finish", 20);
        assert s != null && s.samples() == 4;
        assert Arrays.equals(s.copyForTest(), new byte[]{3, 4, 5, 6, 0, 0, 7, 8});
        assert u.finish("manual_finish", 21) == null;
        assert !u.start("conversation", 22);
        u.release(s);
        assert u.start("enrollment", 30);
        u.accept(new byte[]{1}, 1);
        assert u.finish("manual_finish", 31) == null;
        assert "unaligned_pcm".equals(u.error());
        u.cancel();
        assert u.start("conversation", 40);
        u.accept(new byte[14], 14);
        s = u.sealed();
        assert s != null && s.samples() == 6 && s.endReason.equals("duration_limit");
        u.cancel();
        assert s.isReleased();
        assert u.sealed() == null;
    }
    private static void handoff() {
        PcmHandoff ring = new PcmHandoff(2, 4);
        byte[] reused = {1, 2, 3, 4};
        assert ring.offer(reused, 3);
        Arrays.fill(reused, (byte) 9);
        assert ring.offer(reused, 1);
        assert !ring.offer(reused, 1);
        assert "capture_overflow".equals(ring.error());
        byte[] sink = new byte[4];
        assert ring.poll(sink) == 3;
        assert Arrays.equals(sink, new byte[]{1, 2, 3, 0});
        assert ring.poll(sink) == 1 && sink[0] == 9;
        assert ring.poll(sink) == 0;
        assert ring.highWater() == 2;
        assert !ring.offer(reused, 1); // Overflow stays fatal until a new session.
    }
}

import id.my.yhdev.nexo.audio.core.UploadSession;
import id.my.yhdev.nexo.audio.core.UtteranceBuffer;
import java.util.Arrays;

public final class FixtureHarness {
    private FixtureHarness() {}
    private static byte[] hex(String text) {
        byte[] bytes = new byte[text.length() / 2];
        for (int i = 0; i < bytes.length; i++) bytes[i] = (byte) Integer.parseInt(text.substring(i * 2, i * 2 + 2), 16);
        return bytes;
    }
    public static void main(String[] args) {
        byte[] pcm = hex(args[1]);
        byte[] frame = UploadSession.encode(args[0], 0, pcm);
        assert Arrays.equals(frame, hex(args[2]));
        UtteranceBuffer buffer = new UtteranceBuffer();
        buffer.start("conversation", 0); buffer.accept(pcm, pcm.length);
        assert buffer.finish("manual_finish", 1).sha256().equals(args[3]);
        for (int offset : new int[]{-1, Integer.MAX_VALUE, 320000}) {
            try { UploadSession.encode(args[0], offset, pcm); throw new AssertionError("offset accepted"); }
            catch (IllegalArgumentException expected) { assert expected.getMessage().equals("invalid_length"); }
        }
        System.out.println("PASS fixture");
    }
}

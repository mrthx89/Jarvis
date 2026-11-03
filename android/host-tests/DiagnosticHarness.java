import id.my.yhdev.nexo.audio.core.PrivateDiagnosticClip;
import id.my.yhdev.nexo.audio.core.UtteranceBuffer;
import java.nio.file.*;
import java.io.*;

public final class DiagnosticHarness {
    private DiagnosticHarness() {}
    public static void main(String[] args) throws Exception {
        Path dir = Files.createTempDirectory("nexo-private-clip");
        PrivateDiagnosticClip clip = new PrivateDiagnosticClip(dir.toFile());
        UtteranceBuffer buffer = new UtteranceBuffer();
        buffer.start("diagnostic", 0); buffer.accept(new byte[]{1,2,3,4}, 4);
        UtteranceBuffer.Sealed pcm = buffer.finish("manual_finish", 0);
        assert !clip.retain(pcm, false, 10) && !pcm.isReleased();
        assert clip.retain(pcm, true, 10) && pcm.isReleased();
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        assert !clip.exportWav(out, false, 11) && out.size() == 0;
        assert clip.exportWav(out, true, 11) && out.size() == 48;
        assert out.toByteArray()[44] == 1 && out.toByteArray()[47] == 4;
        clip.expire(300009); assert clip.available(300009);
        clip.expire(300010); assert !clip.available(300010);
        try (java.util.stream.Stream<Path> files = Files.list(dir)) { assert files.count() == 0; }
        Files.delete(dir);
        System.out.println("PASS diagnostic");
    }
}

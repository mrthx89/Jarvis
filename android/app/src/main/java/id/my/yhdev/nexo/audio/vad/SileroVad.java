package id.my.yhdev.nexo.audio.vad;

import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtException;
import ai.onnxruntime.OrtSession;
import id.my.yhdev.nexo.audio.core.LocalCapture;
import java.io.InputStream;
import java.nio.FloatBuffer;
import java.nio.LongBuffer;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;

/** Thread-confined, pinned Silero v6.2.3, 16 kHz CPU only; not speaker recognition. */
public final class SileroVad implements LocalCapture.Vad, AutoCloseable {
    public static final String MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3";
    private final OrtEnvironment environment = OrtEnvironment.getEnvironment();
    private final OrtSession session;
    private final float[] context = new float[64];
    private final float[] input = new float[576];
    private final float[] state = new float[256];

    public SileroVad(String path) {
        try {
            MessageDigest hash = MessageDigest.getInstance("SHA-256");
            try (InputStream file = Files.newInputStream(Paths.get(path))) {
                byte[] block = new byte[8192]; int n;
                while ((n = file.read(block)) != -1) hash.update(block, 0, n);
            }
            StringBuilder hex = new StringBuilder();
            for (byte b : hash.digest()) hex.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
            if (!MODEL_SHA256.equals(hex.toString())) throw new IllegalArgumentException("vad_model_checksum");
            try (OrtSession.SessionOptions options = new OrtSession.SessionOptions()) {
                options.setIntraOpNumThreads(1); options.setInterOpNumThreads(1);
                session = environment.createSession(path, options);
            }
        } catch (Exception e) { throw new IllegalStateException("auto_unavailable", e); }
    }
    @Override public float probability(float[] window) {
        if (window.length != 512) throw new IllegalArgumentException("vad_window");
        System.arraycopy(context, 0, input, 0, 64);
        System.arraycopy(window, 0, input, 64, 512);
        try (OnnxTensor audio = OnnxTensor.createTensor(environment, FloatBuffer.wrap(input), new long[]{1, 576});
             OnnxTensor memory = OnnxTensor.createTensor(environment, FloatBuffer.wrap(state), new long[]{2, 1, 128});
             OnnxTensor rate = OnnxTensor.createTensor(environment, LongBuffer.wrap(new long[]{16000}), new long[]{})) {
            Map<String, OnnxTensor> feeds = new HashMap<>();
            feeds.put("input", audio); feeds.put("state", memory); feeds.put("sr", rate);
            try (OrtSession.Result result = session.run(feeds)) {
                float probability = ((float[][]) result.get(0).getValue())[0][0];
                float[][][] next = (float[][][]) result.get(1).getValue();
                System.arraycopy(next[0][0], 0, state, 0, 128);
                System.arraycopy(next[1][0], 0, state, 128, 128);
                System.arraycopy(window, 448, context, 0, 64);
                return probability;
            }
        } catch (OrtException e) { throw new IllegalStateException("auto_unavailable", e); }
    }
    public void reset() { Arrays.fill(context, 0); Arrays.fill(state, 0); Arrays.fill(input, 0); }
    @Override public void close() {
        reset();
        try { session.close(); } catch (OrtException e) { throw new IllegalStateException("vad_close_failed", e); }
    }
}

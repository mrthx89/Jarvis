import id.my.yhdev.nexo.audio.vad.SileroVad;

public final class SileroHarness {
    private SileroHarness() {}
    public static void main(String[] args) throws Exception {
        try (SileroVad model = new SileroVad(args[0])) {
            float first = model.probability(new float[512]);
            assert Float.isFinite(first) && first >= 0 && first <= 1;
            for (int i = 0; i < 16; i++) model.probability(new float[512]);
            model.reset();
            assert Math.abs(model.probability(new float[512]) - first) < 1e-6;
            System.out.println("PASS silero");
        }
    }
}

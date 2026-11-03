import id.my.yhdev.nexo.audio.core.GenerationGate;

public final class GenerationHarness {
    private GenerationHarness() {}
    public static void main(String[] args) {
        GenerationGate gate = new GenerationGate();
        long old = gate.begin();
        gate.invalidate(old);
        assert !gate.activate(old);
        long fresh = gate.begin();
        gate.invalidate(old);
        assert gate.activate(fresh);
        assert gate.isActive(fresh);
        gate.invalidate(fresh);
        assert !gate.isActive(fresh);
        assert !gate.activate(fresh);
        System.out.println("PASS generation");
    }
}

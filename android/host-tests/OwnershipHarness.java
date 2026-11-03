import id.my.yhdev.nexo.audio.core.AudioOwnership;

/** SDK-free execution of the same ownership gate used by the Android service. */
public final class OwnershipHarness {
    private static final String C = "00000000-0000-4000-8000-000000000010";
    private static final String O = "00000000-0000-4000-8000-000000000011";
    private static final String R = "00000000-0000-4000-8000-000000000012";
    private static final String L = "00000000-0000-4000-8000-000000000013";
    private OwnershipHarness() { }
    public static void main(String[] args) {
        AudioOwnership gate = new AudioOwnership();
        gate.foreground(true);
        gate.connected(C, 0);
        long token = gate.openSheet(O, 0);
        assert !gate.normalAllowed();
        assert !gate.intent();
        assert gate.acquire(R, 0);
        gate.localStopped(token, 10);
        assert !gate.grant(C, R, O, L, 15000, true, 1, 20);
        assert !gate.dedicatedAllowed(609);
        assert gate.dedicatedAllowed(610);
        assert !gate.normalAllowed();
        assert !gate.intent() : "dedicated capture must work while conversation OFF";
        for (String exit : new String[]{"cancel", "swipe", "back", "background", "timeout", "disconnect", "STOP", "permission", "route"}) {
            gate.closeSheet("00000000-0000-4000-8000-000000000014", 700);
            assert !gate.dedicatedAllowed(700) : exit;
            assert !gate.grant(C, R, O, L, 15000, true, 2, 710) : exit;
            assert !gate.normalAllowed() : exit;
        }
        assert !gate.resume(R, 800) : "release ACK required";
        assert !gate.released(C, R, O, true, 2) : "wrong request";
        assert gate.released(C, "00000000-0000-4000-8000-000000000014", O, true, 2);
        assert gate.resume(R, 800);
        assert !gate.resumed(C, "00000000-0000-4000-8000-000000000014", false, 3);
        assert gate.resumed(C, R, false, 3);
        assert !gate.normalAllowed() : "hardware not ready";
        long resumedToken = gate.generation();
        assert gate.recorderReady(resumedToken, true);
        assert gate.normalAllowed();
        gate.openSheet(O, 1000);
        assert !gate.normalAllowed();
        assert !gate.recorderReady(resumedToken, true) : "late hardware callback";
        assert !gate.resumed(C, R, false, 4) : "late resume loses to sheet";
        assert !gate.outputAllowed(C, 3);
        AudioOwnership renewed = new AudioOwnership();
        renewed.foreground(true); renewed.connected(C, 0);
        long rt = renewed.openSheet(O, 0);
        assert renewed.acquire(R, 0);
        renewed.localStopped(rt, 0);
        renewed.grant(C, R, O, L, 15000, true, 1, 1);
        assert renewed.dedicatedAllowed(600);
        assert renewed.renewDue(5001);
        assert renewed.renew("00000000-0000-4000-8000-000000000015", 5001);
        assert !renewed.renewed(C, R, O, L, 15000, 5002);
        assert renewed.renewed(C, "00000000-0000-4000-8000-000000000015", O, L, 15000, 5002);
        assert renewed.dedicatedAllowed(7002);
        assert renewed.renew("00000000-0000-4000-8000-000000000016", 10002);
        assert !renewed.dedicatedAllowed(12002) : "missing renew ACK";
        assert renewed.expired(12002);
        renewed.disconnected();
        renewed.connected(C, 4);
        assert !renewed.normalAllowed() : "reconnect cannot resume";
        assert !renewed.dedicatedAllowed(12003);
        assert renewed.resume(R, 13000);
        assert renewed.expired(16000) : "resume ACK deadline";
        renewed.disconnected(); renewed.connected(C, 5);
        assert renewed.resume(R, 17000);
        assert renewed.resumed(C, R, false, 6);
        assert !renewed.recorderReady(renewed.generation(), false);
        assert !renewed.normalAllowed();
        assert renewed.recorderReady(renewed.generation(), true);
        assert renewed.outputAllowed(C, 6);
        renewed.foreground(false);
        assert !renewed.normalAllowed();
        renewed.foreground(true);
        assert !renewed.normalAllowed() : "foreground return cannot resume";
        System.out.println("PASS ownership");
    }
}

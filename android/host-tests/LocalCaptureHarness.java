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
        id.my.yhdev.nexo.audio.core.ConversationRecovery recovery = new id.my.yhdev.nexo.audio.core.ConversationRecovery();
        String instance = "00000000-0000-4000-8000-000000000020";
        recovery.remember(instance);
        long offline = capture.arm("conversation", false, null);
        capture.start(offline, 100);
        capture.accept(offline, new byte[]{1,2}, 2, 101);
        assert recovery.disconnected("conversation", capture.state(), false);
        assert recovery.active() && recovery.instance().equals(instance);
        capture.accept(offline, new byte[]{3,4}, 2, 102);
        UtteranceBuffer.Sealed retained = capture.finish(offline, 103);
        assert Arrays.equals(retained.copyForTest(), new byte[]{1,2,3,4}) : "fault cannot truncate local capture";
        id.my.yhdev.nexo.audio.core.AudioOwnership owner = new id.my.yhdev.nexo.audio.core.AudioOwnership();
        owner.foreground(true); owner.connected(instance, 0);
        owner.disconnected(); owner.connected(instance, 1);
        java.util.List<java.util.Map<String,Object>> controls = new java.util.ArrayList<>();
        id.my.yhdev.nexo.audio.core.UploadSession retry = new id.my.yhdev.nexo.audio.core.UploadSession(
            new id.my.yhdev.nexo.audio.core.UploadSession.Transport() {
                public boolean control(java.util.Map<String,Object> value) { controls.add(value); return true; }
                public boolean binary(byte[] value) { return true; }
                public long queuedBytes() { return 0; }
            });
        assert retry.retainDisconnected(retained, recovery.instance(), 104);
        retry.connect(instance, instance, 2, true, 105);
        assert controls.get(0).get("type").equals("utterance_status");
        retry.ack(instance, (String) controls.get(0).get("request_id"), retained.id, "unknown", 0, 0, null, 106);
        assert retry.state().equals("RETRY_READY") && controls.size() == 1;
        assert !owner.normalAllowed() && !owner.intent() : "status recovery cannot resume mic";
        retry.cancel(107);
        assert retained.isReleased() && !retry.resumeRetry(108) : "privacy exit wins";
        for (String state : new String[]{"BEGIN", "SENDING", "COMMIT", "ACCEPTED"}) {
            recovery.clear(); recovery.remember(instance);
            assert recovery.disconnected("conversation", "SEALED", true) : state;
        }
        recovery.clear(); recovery.remember(instance);
        assert !recovery.disconnected("enrollment", "CAPTURING", true);
        assert !recovery.active();
        assert !recovery.disconnected("conversation", "ARMED", false) : "idle VAD cannot keep listening after loss";
        capture.cancel("background"); recovery.clear();
        assert retained.isReleased() && !recovery.active();
        for (String purpose : new String[]{"conversation", "enrollment", "diagnostic"}) {
            LocalCapture capped = new LocalCapture(); long t = capped.arm(purpose, false, null);
            capped.start(t, 0);
            for (int i = 0; i < 200; i++) capped.accept(t, new byte[3200], 3200, i);
            UtteranceBuffer.Sealed s = capped.sealed();
            assert s != null && s.samples() == 320000 && s.endReason.equals("duration_limit");
            assert s.status("Mengirim").contains("batas 20 detik") : purpose;
            assert s.status("Klip siap").contains("duration_limit") : purpose;
            assert s.samples() == 320000 && s.endReason.equals("duration_limit");
        }
        System.out.println("PASS local");
    }
}

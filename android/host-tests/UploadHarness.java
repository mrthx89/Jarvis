import id.my.yhdev.nexo.audio.core.UploadSession;
import id.my.yhdev.nexo.audio.core.UtteranceBuffer;
import java.util.*;

public final class UploadHarness {
    private UploadHarness() {}
    private static final class Wire implements UploadSession.Transport {
        final List<Map<String, Object>> controls = new ArrayList<>();
        final List<byte[]> frames = new ArrayList<>();
        long queued;
        int aborted;
        public void abort() { aborted++; }
        public boolean control(Map<String, Object> value) { controls.add(value); return true; }
        public boolean binary(byte[] value) { frames.add(value); return true; }
        public long queuedBytes() { return queued; }
    }
    public static void main(String[] args) {
        Wire wire = new Wire();
        UploadSession upload = new UploadSession(wire);
        UtteranceBuffer buffer = new UtteranceBuffer();
        assert buffer.start("conversation", 0);
        buffer.accept(new byte[]{0,0,1,0,-1,-1,-1,127,0,-128,100,0,-100,-1,0,0}, 16);
        UtteranceBuffer.Sealed pcm = buffer.finish("manual_finish", 100);
        String connection = "00000000-0000-4000-8000-000000000010";
        String instance = "00000000-0000-4000-8000-000000000020";
        assert upload.connect(connection, instance, 16384, true, 0);
        assert upload.begin(pcm, null, null, 101);
        assert wire.controls.size() == 1 && wire.frames.isEmpty();
        String request = (String) wire.controls.get(0).get("request_id");
        upload.ack(connection, request, pcm.id, "begin", 0, 0, null, 102);
        upload.pump(103);
        assert wire.frames.size() == 1;
        byte[] frame = wire.frames.get(0);
        assert frame.length == 44 && frame[0] == 'N' && frame[3] == '2' && frame[27] == 16;
        assert wire.controls.get(1).get("type").equals("utterance_commit");
        request = (String) wire.controls.get(1).get("request_id");
        String digest = pcm.sha256();
        assert digest.equals("089b7ba2e8ba613255a1812dca037fd83d79aff20f983860d9d5ee4300dcac55");
        upload.ack(connection, request, pcm.id, "accepted", 0, 8, digest, 104);
        assert upload.state().equals("ACCEPTED");
        upload.ack(connection, request, pcm.id, "accepted", 0, 8, digest, 105);
        upload.pump(106);
        assert wire.frames.size() == 1 && wire.controls.size() == 2;
        assert pcm.isReleased(); // Integrity ACK releases raw PCM immediately.
        assert upload.state().equals("ACCEPTED");
        upload.pump(30100);
        assert upload.state().equals("ACCEPTED");
        assert !upload.begin(pcm, null, null, 30101);
        upload.disconnected();
        assert upload.connect(connection, instance, 16384, true, 30101);
        assert wire.controls.get(wire.controls.size() - 1).get("type").equals("utterance_status");
        request = (String) wire.controls.get(wire.controls.size() - 1).get("request_id");
        upload.ack(connection, request, pcm.id, "accepted", 0, 8, digest, 30101);
        assert upload.state().equals("ACCEPTED");
        upload.cancel(30102);
        assert wire.controls.get(wire.controls.size() - 1).get("type").equals("utterance_cancel");
        upload.disconnected();
        int before = wire.controls.size();
        upload.connect(connection, instance, 16384, true, 200);
        assert wire.controls.size() == before; // Cancel can never become status/replay.
        assert pcm.isReleased();
        upload.clear();
        UtteranceBuffer retryBuffer = new UtteranceBuffer();
        retryBuffer.start("conversation", 0); retryBuffer.accept(new byte[]{1, 2}, 2);
        UtteranceBuffer.Sealed retry = retryBuffer.finish("manual_finish", 0);
        assert upload.begin(retry, null, null, 1);
        upload.disconnected(); upload.pump(30000);
        assert retry.isReleased() && upload.error().equals("expired");
        upload.clear(); upload.connect(connection, instance, 16384, true, 0);
        UtteranceBuffer cancelBuffer = new UtteranceBuffer();
        cancelBuffer.start("conversation", 0); cancelBuffer.accept(new byte[]{1, 2}, 2);
        UtteranceBuffer.Sealed cancelled = cancelBuffer.finish("manual_finish", 0);
        assert upload.begin(cancelled, null, null, 1);
        upload.cancel(2);
        assert cancelled.isReleased() && upload.state().equals("CANCELLING");
        upload.pump(2001); assert upload.state().equals("CANCELLING");
        upload.pump(2002); assert upload.error().equals("cancel_timeout");
        assert wire.aborted == 1;
        for (String scenario : new String[]{"retry", "restart", "checksum", "sequence", "enrollment"}) {
            Wire w = new Wire(); UploadSession u = new UploadSession(w);
            UtteranceBuffer b = new UtteranceBuffer();
            String purpose = scenario.equals("enrollment") ? "enrollment" : "conversation";
            b.start(purpose, 0); b.accept(new byte[]{1, 2, 3, 4}, 4);
            UtteranceBuffer.Sealed s = b.finish("manual_finish", 0);
            u.connect(connection, instance, 2, true, 0);
            assert u.begin(s, purpose.equals("enrollment") ? connection : null,
                purpose.equals("enrollment") ? instance : null, 1);
            String r = (String) w.controls.get(0).get("request_id");
            u.ack("00000000-0000-4000-8000-000000000099", r, s.id, "begin", 0, 0, null, 2);
            assert u.state().equals("BEGIN");
            u.ack(connection, r, s.id, "begin", 0, 0, null, 2);
            w.queued = 131072; u.pump(3); assert w.frames.isEmpty();
            w.queued = 0; u.pump(4); assert w.frames.size() == 1;
            if (scenario.equals("sequence")) {
                u.ack(connection, r, s.id, "receiving", 3, 0, null, 5);
                assert u.error().equals("sequence_gap") && s.isReleased();
            } else if (scenario.equals("checksum")) {
                u.pump(5); r = (String) w.controls.get(1).get("request_id");
                u.ack(connection, r, s.id, "accepted", 0, 2, "0".repeat(64), 6);
                assert u.error().equals("checksum_mismatch") && s.isReleased();
            } else {
                u.disconnected();
                String nextInstance = scenario.equals("restart") ? connection : instance;
                u.connect(connection, nextInstance, 2, true, 6);
                if (scenario.equals("restart")) assert u.error().equals("delivery_unknown") && s.isReleased();
                else if (scenario.equals("enrollment")) assert s.isReleased() && w.controls.size() == 1;
                else {
                    assert w.controls.get(1).get("type").equals("utterance_status");
                    r = (String) w.controls.get(1).get("request_id");
                    u.ack(connection, r, s.id, "unknown", 0, 0, null, 7);
                    assert w.controls.get(2).get("utterance_id").equals(s.id);
                    r = (String) w.controls.get(2).get("request_id");
                    u.ack(connection, r, s.id, "begin", 0, 0, null, 8); u.pump(9);
                    assert java.nio.ByteBuffer.wrap(w.frames.get(1), 20, 4).getInt() == 0;
                }
            }
        }
        upload.clear(); upload.connect(connection, instance, 16384, true, 0);
        UtteranceBuffer doneBuffer = new UtteranceBuffer();
        doneBuffer.start("conversation", 0); doneBuffer.accept(new byte[]{1, 2}, 2);
        UtteranceBuffer.Sealed done = doneBuffer.finish("manual_finish", 0);
        upload.begin(done, null, null, 1);
        request = (String) wire.controls.get(wire.controls.size() - 1).get("request_id");
        upload.ack(connection, request, done.id, "begin", 0, 0, null, 2); upload.pump(3);
        request = (String) wire.controls.get(wire.controls.size() - 1).get("request_id");
        upload.ack(connection, request, done.id, "completed", 0, 1, done.sha256(), 4);
        UtteranceBuffer freshBuffer = new UtteranceBuffer();
        freshBuffer.start("conversation", 5); freshBuffer.accept(new byte[]{1, 2}, 2);
        assert upload.begin(freshBuffer.finish("manual_finish", 6), null, null, 7);
        System.out.println("PASS upload");
    }
}

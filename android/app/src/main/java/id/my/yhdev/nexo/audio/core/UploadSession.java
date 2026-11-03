package id.my.yhdev.nexo.audio.core;

import java.nio.ByteBuffer;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/** Serialized upload worker only. Local queue admission is never an integrity ACK. */
public final class UploadSession {
    public interface Transport {
        boolean control(Map<String,Object> value);
        boolean binary(byte[] value);
        long queuedBytes();
        default void abort() {}
    }
    private final Transport wire;
    private UtteranceBuffer.Sealed pcm;
    private String id;
    private int acceptedSamples;
    private String acceptedHash;
    private String connection;
    private String instance;
    private String originalInstance;
    private String lease;
    private String enrollment;
    private String request;
    private String state = "PAUSED";
    private String error;
    private int chunkBytes;
    private int sentSamples;
    private int confirmed;
    private long deadline;
    private boolean supported;

    public UploadSession(Transport wire) { this.wire = wire; }
    public synchronized boolean connect(String connectionId, String instanceId, int maxChunk, boolean selected, long now) {
        uuid(connectionId); uuid(instanceId);
        connection = connectionId; instance = instanceId;
        supported = selected && maxChunk >= 2 && maxChunk <= 16384 && (maxChunk & 1) == 0;
        chunkBytes = maxChunk;
        if (!supported) { fail("unsupported_capability"); return false; }
        if (state.equals("CANCELLING")) { fail("cancelled"); return false; }
        if (pcm == null) {
            if (id != null && state.equals("ACCEPTED")) {
                if (!instance.equals(originalInstance)) { fail("delivery_unknown"); return false; }
                state = "ACCEPTED_STATUS"; deadline = now + 5000; return send("utterance_status");
            }
            return true;
        }
        if (expired(now)) { fail("expired"); return false; }
        if (!instance.equals(originalInstance)) { fail("delivery_unknown"); return false; }
        if (!pcm.purpose.equals("conversation")) { fail("cancelled"); return false; }
        state = "STATUS"; deadline = now + 5000;
        return send("utterance_status");
    }
    public synchronized boolean begin(UtteranceBuffer.Sealed sealed, String leaseId, String enrollmentId, long now) {
        if (state.equals("COMPLETED")) id = null;
        if (!supported || connection == null || id != null || sealed.isReleased()) return false;
        if (sealed.purpose.equals("diagnostic")) return false;
        if (sealed.purpose.equals("enrollment")) { uuid(leaseId); uuid(enrollmentId); }
        if (now - sealed.sealedAt >= 30000 || now < sealed.sealedAt) { fail("expired"); return false; }
        pcm = sealed; id = sealed.id; lease = leaseId; enrollment = enrollmentId; originalInstance = instance;
        error = null;
        return startBegin(now);
    }
    private boolean startBegin(long now) {
        sentSamples = 0; confirmed = 0; state = "BEGIN"; deadline = now + 5000;
        return send("utterance_begin");
    }
    private boolean send(String type) {
        Map<String,Object> value = envelope(type);
        if (id != null) value.put("utterance_id", id);
        if (pcm != null) {
            if (type.equals("utterance_begin") || type.equals("utterance_commit")) {
                value.put("total_samples", pcm.samples()); value.put("sha256", pcm.sha256());
            }
            if (type.equals("utterance_begin")) {
                value.put("purpose", pcm.purpose); value.put("end_reason", pcm.endReason);
                if (lease != null) { value.put("lease_id", lease); value.put("enrollment_id", enrollment); }
            }
        }
        if (!wire.control(value)) { state = "DISCONNECTED"; error = "transport_unavailable"; return false; }
        return true;
    }
    private Map<String,Object> envelope(String type) {
        request = UUID.randomUUID().toString();
        Map<String,Object> value = new LinkedHashMap<>();
        value.put("type", type); value.put("connection_id", connection); value.put("request_id", request);
        return value;
    }
    public synchronized void ack(String connectionId, String requestId, String utteranceId, String stage,
                                  int next, int samples, String hash, long now) {
        if (!connectionId.equals(connection) || !utteranceId.equals(id)) return;
        if (state.equals("CANCELLING")) {
            if (request.equals(requestId) && stage.equals("cancelled")) { id = null; state = "CANCELLED"; }
            return;
        }
        if (state.equals("ACCEPTED_STATUS")) {
            if (!request.equals(requestId)) return;
            if ((stage.equals("accepted") || stage.equals("completed")) && samples == acceptedSamples && acceptedHash.equals(hash)) {
                state = stage.equals("accepted") ? "ACCEPTED" : "COMPLETED";
            } else fail("delivery_unknown");
            return;
        }
        if (pcm == null || terminal()) return;
        if (expired(now)) { fail("expired"); return; }
        if (stage.equals("receiving")) {
            if (!state.equals("SENDING") && !state.equals("COMMIT")) return;
            if (next < confirmed || next > sentSamples) { fail("sequence_gap"); return; }
            confirmed = next; return;
        }
        if (!request.equals(requestId)) return;
        if (stage.equals("begin") && state.equals("BEGIN")) {
            if (next != 0) { fail("sequence_gap"); return; }
            state = "SENDING"; deadline = now + 30000;
        } else if (stage.equals("accepted") || stage.equals("completed")) {
            if (!state.equals("COMMIT") && !state.equals("STATUS") && !state.equals("BEGIN")) return;
            if (samples != pcm.samples() || !pcm.sha256().equals(hash)) { fail("checksum_mismatch"); return; }
            state = stage.equals("accepted") ? "ACCEPTED" : "COMPLETED";
            acceptedSamples = samples; acceptedHash = hash;
            pcm.erase(); pcm = null;
        } else if (stage.equals("unknown") && state.equals("STATUS")) {
            startBegin(now);
        } else if (stage.equals("cancelled") || stage.equals("rejected")) {
            fail(stage);
        }
    }
    public synchronized void pump(long now) {
        if (state.equals("CANCELLING")) {
            if (now >= deadline) { fail("cancel_timeout"); wire.abort(); }
            return;
        }
        if (state.equals("ACCEPTED_STATUS") && now >= deadline) { fail("delivery_unknown"); wire.abort(); return; }
        // Disconnected retries retain the original seal deadline, not an infinite cache.
        if (pcm != null && expired(now)) { fail("expired"); return; }
        if (pcm == null || terminal() || state.equals("DISCONNECTED")) return;
        if (now >= deadline) { state = "DISCONNECTED"; error = "upload_timeout"; return; }
        if (!state.equals("SENDING")) return;
        int bytes = Math.min(chunkBytes, (pcm.samples() - sentSamples) * 2);
        if (bytes > 0) {
            if (wire.queuedBytes() + 2L * (bytes + 28) + 4096 > 131072) return;
            byte[] payload = pcm.chunk(sentSamples, bytes);
            if (!wire.binary(encode(id, sentSamples, payload))) {
                state = "DISCONNECTED"; error = "transport_unavailable"; return;
            }
            sentSamples += payload.length / 2;
        }
        if (sentSamples == pcm.samples()) {
            state = "COMMIT"; deadline = now + 5000; send("utterance_commit");
        }
    }
    public synchronized void disconnected() {
        connection = null; supported = false;
        if (state.equals("CANCELLING")) { fail("cancelled"); return; }
        if (pcm != null && !terminal()) state = "DISCONNECTED";
        if (pcm != null && !pcm.purpose.equals("conversation")) fail("cancelled");
    }
    public synchronized void cancel(long now) {
        if (state.equals("CANCELLING")) return;
        boolean sent = false;
        if (id != null && connection != null && !state.equals("COMPLETED")) {
            Map<String,Object> value = envelope("utterance_cancel");
            value.put("utterance_id", id); value.put("reason", "cancelled"); sent = wire.control(value);
        }
        if (pcm != null) pcm.erase();
        pcm = null; error = "cancelled";
        if (sent) { state = "CANCELLING"; deadline = now + 2000; }
        else { id = null; state = "CANCELLED"; }
    }
    public synchronized void clear() { if (pcm != null) pcm.erase(); pcm = null; id = null; state = "PAUSED"; error = null; }
    public synchronized void serverError(String utteranceId, String code) {
        if (utteranceId.equals(id) && !terminal()) fail(code);
    }
    /** Service calls only for a current matched terminal result, never to resume listening. */
    public synchronized void result(String utteranceId) {
        if (utteranceId.equals(id) && (state.equals("ACCEPTED") || state.equals("COMPLETED"))) {
            id = null; state = "COMPLETED";
        }
    }
    private boolean expired(long now) { return pcm.isReleased() || now < pcm.sealedAt || now - pcm.sealedAt >= 30000; }
    private boolean terminal() { return state.equals("ERROR") || state.equals("ACCEPTED") || state.equals("COMPLETED"); }
    private void fail(String code) {
        error = code; state = "ERROR";
        if (pcm != null) pcm.erase();
        pcm = null; id = null;
    }
    public synchronized String state() { return state; }
    public synchronized String error() { return error; }
    public static UUID uuid(String value) {
        if (value == null) throw new IllegalArgumentException("invalid_uuid");
        UUID id = UUID.fromString(value);
        if (!id.toString().equals(value)) throw new IllegalArgumentException("invalid_uuid");
        return id;
    }
    public static byte[] encode(String id, int sampleOffset, byte[] payload) {
        UUID parsed = uuid(id);
        if (payload.length < 2 || payload.length > 16384 || (payload.length & 1) != 0 ||
            sampleOffset < 0 || (long) sampleOffset + payload.length / 2 > UtteranceBuffer.MAX_SAMPLES)
            throw new IllegalArgumentException("invalid_length");
        ByteBuffer frame = ByteBuffer.allocate(28 + payload.length);
        frame.putInt(0x4e584132).putLong(parsed.getMostSignificantBits()).putLong(parsed.getLeastSignificantBits())
            .putInt(sampleOffset).putInt(payload.length).put(payload);
        return frame.array();
    }
}

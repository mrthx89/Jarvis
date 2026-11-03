package id.my.yhdev.nexo.audio.core;

/** Conservative local lease barrier. The service supplies confirmed hardware stop, never a UI flag. */
public final class CaptureLease {
    private long generation;
    private String connection;
    private String owner;
    private String lease;
    private long acquireDeadline;
    private long expiry;
    private long echoUntil = Long.MAX_VALUE;
    private long renewAt;
    private long renewDeadline;
    private boolean open;
    public synchronized long open(String connectionId, String ownerId, long now) {
        close(); UploadSession.uuid(connectionId); UploadSession.uuid(ownerId);
        connection = connectionId; owner = ownerId; open = true; acquireDeadline = now + 3000;
        return generation;
    }
    public synchronized boolean grant(long token, String connectionId, String ownerId, String leaseId, int ttl, long now) {
        if (!matches(token, connectionId, ownerId) || now >= acquireDeadline || lease != null) return false;
        UploadSession.uuid(leaseId);
        if (ttl < 1 || ttl > 15000) { close(); return false; }
        lease = leaseId; expiry = now + ttl; renewAt = now + 5000;
        return ready(now);
    }
    public synchronized void localStopped(long token, long now) {
        if (open && token == generation && echoUntil == Long.MAX_VALUE) echoUntil = now + 600;
    }
    public synchronized boolean ready(long now) {
        if (!open) return false;
        if ((lease == null || echoUntil == Long.MAX_VALUE || echoUntil > acquireDeadline) && now >= acquireDeadline) {
            close(); return false;
        }
        if ((lease != null && now >= expiry) || (renewDeadline > 0 && now >= renewDeadline)) { close(); return false; }
        return lease != null && now >= echoUntil;
    }
    public synchronized boolean canCapture(long token, String leaseId, long now) {
        return token == generation && leaseId != null && leaseId.equals(lease) && ready(now);
    }
    public synchronized void close() {
        generation++; open = false; lease = null; connection = null; owner = null;
        echoUntil = Long.MAX_VALUE; renewDeadline = 0; expiry = 0;
    }
    public synchronized boolean renewDue(long now) { return ready(now) && renewDeadline == 0 && now >= renewAt; }
    public synchronized void renewSent(long token, long now) {
        if (token == generation && renewDue(now)) renewDeadline = now + 2000;
    }
    public synchronized boolean renewed(long token, String connectionId, String ownerId, String leaseId, int ttl, long now) {
        if (!ready(now) || !matches(token, connectionId, ownerId) || !lease.equals(leaseId) || renewDeadline == 0) return false;
        if (ttl < 1 || ttl > 15000) { close(); return false; }
        expiry = now + ttl; renewAt = now + 5000; renewDeadline = 0; return true;
    }
    private boolean matches(long token, String connectionId, String ownerId) {
        return open && generation == token && connection.equals(connectionId) && owner.equals(ownerId);
    }
}

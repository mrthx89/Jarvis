package id.my.yhdev.nexo.audio.core;

/** Local service ownership policy. Hardware confirmation is supplied by its lifecycle worker. */
public final class AudioOwnership {
    private final CaptureLease barrier = new CaptureLease();
    private final Object outputSerial = new Object();

    /** Admission is ordered with revocation; hardware never owns the policy monitor.
     * An already admitted operation may finish; readiness still requires physical quiescence.
     */
    public boolean runOutput(String id, long value, Runnable action) {
        synchronized (outputSerial) {
            synchronized (this) { if (!outputAllowed(id, value)) return false; }
            action.run();
            return true;
        }
    }
    private String connection;
    private String owner;
    private String lease;
    private String acquireRequest;
    private long generation;
    private long leaseToken;
    private long epoch;
    private long deadline;
    private boolean foreground;
    private String releaseRequest;
    private String resumeRequest;
    private boolean intent;
    private boolean resumed;
    private boolean recorder;
    private boolean releasing;

    private String renewRequest;
    private boolean everReady;
    public synchronized void foreground(boolean value) {
        foreground = value;
        if (!value) { generation++; intent = false; resumed = false; recorder = false; barrier.close(); }
    }
    public synchronized void connected(String id, long outputEpoch) {
        disconnected(); UploadSession.uuid(id); connection = id; epoch = outputEpoch;
    }
    public synchronized long openSheet(String id, long now) {
        UploadSession.uuid(id); generation++; owner = id; lease = null; acquireRequest = null;
        barrier.close(); everReady = false; intent = false; resumed = false; recorder = false; releasing = false;
        resumeRequest = null; releaseRequest = null; deadline = now + 3000; return generation;
    }
    public synchronized boolean normalAllowed() { return foreground && intent && resumed && recorder && owner == null; }
    public synchronized boolean intent() { return intent; }
    public synchronized boolean pauseForModeChange(boolean busy) {
        if (busy || owner != null || releasing || resumeRequest != null) return false;
        disconnected();
        return true;
    }
    public synchronized boolean acquire(String request, long now) {
        if (!foreground || connection == null || owner == null || now >= deadline || acquireRequest != null) return false;
        UploadSession.uuid(request); acquireRequest = request;
        leaseToken = barrier.open(connection, owner, now); return true;
    }
    public synchronized void localStopped(long token, long now) {
        if (token == generation) barrier.localStopped(leaseToken, now);
    }
    public synchronized boolean grant(String id, String request, String ownerId, String leaseId,
            int ttl, boolean paused, long outputEpoch, long now) {
        if (!paused || !foreground || !matches(id, request, ownerId) || now >= deadline || outputEpoch <= epoch) return false;
        epoch = outputEpoch; lease = leaseId;
        barrier.grant(leaseToken, id, ownerId, leaseId, ttl, now);
        return dedicatedAllowed(now);
    }
    public synchronized void closeSheet(String request, long now) {
        if (owner == null || releasing) return;
        UploadSession.uuid(request); releaseRequest = request; releasing = true;
        generation++; intent = false; resumed = false; recorder = false; resumeRequest = null;
        barrier.close(); deadline = now + 2000;
    }
    public synchronized boolean resume(String request, long now) {
        if (!foreground || connection == null || owner != null || resumeRequest != null) return false;
        UploadSession.uuid(request); generation++; resumeRequest = request;
        intent = true; resumed = false; recorder = false; deadline = now + 3000; return true;
    }
    public synchronized boolean released(String id, String request, String ownerId, boolean paused, long value) {
        if (!releasing || !paused || !same(connection, id) || !same(owner, ownerId) ||
                !same(releaseRequest, request) || value < epoch) return false;
        epoch = value; owner = null; lease = null; releasing = false; releaseRequest = null; return true;
    }
    public synchronized boolean resumed(String id, String request, boolean paused, long value) {
        if (paused || !foreground || owner != null || !intent || !same(connection, id) ||
                !same(resumeRequest, request) || value <= epoch) return false;
        epoch = value; resumed = true; resumeRequest = null; return true;
    }
    public synchronized long generation() { return generation; }
    public synchronized boolean recorderReady(long token, boolean success) {
        if (token != generation || !resumed || owner != null || !foreground || !intent) return false;
        recorder = success; return success;
    }
    public synchronized boolean outputAllowed(String id, long value) {
        return normalAllowed() && same(connection, id) && epoch == value;
    }
    public synchronized boolean renewDue(long now) { return !releasing && barrier.renewDue(now); }
    public synchronized boolean renew(String request, long now) {
        if (!renewDue(now)) return false;
        UploadSession.uuid(request); renewRequest = request; barrier.renewSent(leaseToken, now); return true;
    }
    public synchronized boolean renewed(String id, String request, String ownerId, String leaseId, int ttl, long now) {
        if (releasing || !same(renewRequest, request)) return false;
        boolean ok = barrier.renewed(leaseToken, id, ownerId, leaseId, ttl, now);
        if (ok) renewRequest = null;
        return ok;
    }
    public synchronized boolean expired(long now) {
        if (owner != null) {
            boolean ready = dedicatedAllowed(now);
            if (ready) everReady = true;
            return releasing ? now >= deadline : (!ready && (everReady || now >= deadline));
        }
        return resumeRequest != null && now >= deadline;
    }
    public synchronized void disconnected() {
        generation++; connection = null; owner = null; lease = null; acquireRequest = null;
        releaseRequest = null; resumeRequest = null; renewRequest = null;
        intent = false; resumed = false; recorder = false; releasing = false; everReady = false; barrier.close();
    }
    public synchronized String owner() { return owner; }
    public synchronized String lease() { return lease; }
    public synchronized long epoch() { return epoch; }
    public synchronized String connection() { return connection; }
    public synchronized boolean releasing() { return releasing; }
    private static boolean same(String left, String right) { return left != null && left.equals(right); }
    private boolean matches(String id, String request, String ownerId) {
        return connection != null && connection.equals(id) && owner != null && owner.equals(ownerId)
                && acquireRequest != null && acquireRequest.equals(request);
    }
    public synchronized boolean dedicatedAllowed(long now) {
        return foreground && lease != null && barrier.canCapture(leaseToken, lease, now);
    }
}

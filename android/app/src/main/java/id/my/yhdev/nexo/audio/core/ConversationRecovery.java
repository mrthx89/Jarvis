package id.my.yhdev.nexo.audio.core;

/** Volatile retry intent is separate from normal microphone intent. */
public final class ConversationRecovery {
    private volatile String instance;
    private volatile boolean active;
    public void remember(String value) { if (!active) instance = value; }
    public boolean disconnected(String purpose, String captureState, boolean pending) {
        active = instance != null && purpose.equals("conversation") &&
            (pending || captureState.equals("CAPTURING") || captureState.equals("SEALED"));
        return active;
    }
    public boolean active() { return active; }
    public String instance() { return instance; }
    public void clear() { active = false; instance = null; }
}

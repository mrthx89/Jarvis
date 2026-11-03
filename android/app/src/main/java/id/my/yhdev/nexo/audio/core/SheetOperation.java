package id.my.yhdev.nexo.audio.core;

/** Main-thread completion admission for async sheet work. */
public final class SheetOperation {
    private long generation;
    private String owner;
    private String state;
    private long capture;
    public long begin(String ownerId, String expectedState, long captureGeneration) {
        owner = ownerId; state = expectedState; capture = captureGeneration;
        return ++generation;
    }
    public void invalidate() { generation++; owner = null; }
    public boolean accepts(long token, String ownerId, String currentState, long captureGeneration) {
        return token == generation && owner != null && owner.equals(ownerId) &&
            state.equals(currentState) && capture == captureGeneration;
    }
}

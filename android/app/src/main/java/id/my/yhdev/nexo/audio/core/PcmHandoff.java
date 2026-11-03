package id.my.yhdev.nexo.audio.core;

import java.util.Arrays;

/** Single producer, single consumer; one instance per recorder generation. */
public final class PcmHandoff {
    private final byte[][] slots;
    private final int[] sizes;
    private volatile long written;
    private volatile long read;
    private volatile String failure;
    private volatile int highWater;

    public PcmHandoff(int count, int slotBytes) {
        if (count < 1 || slotBytes < 1) throw new IllegalArgumentException("handoff_budget");
        slots = new byte[count][slotBytes];
        sizes = new int[count];
    }
    public boolean offer(byte[] bytes, int count) {
        if (failure != null) return false;
        if (count < 1 || count > slots[0].length || count > bytes.length) {
            fail("invalid_read"); return false;
        }
        long w = written;
        if (w - read == slots.length) { fail("capture_overflow"); return false; }
        int index = (int) (w % slots.length);
        System.arraycopy(bytes, 0, slots[index], 0, count);
        sizes[index] = count;
        highWater = Math.max(highWater, (int) (w - read + 1));
        written = w + 1;
        return true;
    }
    public int poll(byte[] destination) {
        long r = read;
        if (r == written) return 0;
        int index = (int) (r % slots.length);
        int count = sizes[index];
        if (destination.length < count) throw new IllegalArgumentException("small_destination");
        System.arraycopy(slots[index], 0, destination, 0, count);
        Arrays.fill(slots[index], 0, count, (byte) 0);
        read = r + 1;
        return count;
    }
    public void fail(String code) { if (failure == null) failure = code; }
    public String error() { return failure; }
    public int highWater() { return highWater; }
    public long pending() { return written - read; }
    public void clearAfterJoin() {
        for (byte[] slot : slots) Arrays.fill(slot, (byte) 0);
        read = written;
    }
}

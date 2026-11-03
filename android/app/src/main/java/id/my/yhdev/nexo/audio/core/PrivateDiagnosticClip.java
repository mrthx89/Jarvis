package id.my.yhdev.nexo.audio.core;

import java.io.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Arrays;

/** Explicit-consent, single private clip. Owner must expire periodically and delete on every exit. */
public final class PrivateDiagnosticClip implements AutoCloseable {
    private final File file;
    private long retainedAt;
    private int bytes;
    public PrivateDiagnosticClip(File privateDirectory) throws IOException {
        if (!privateDirectory.isDirectory() && !privateDirectory.mkdirs()) throw new IOException("diagnostic_storage");
        file = new File(privateDirectory, "local-test.wav");
        close(); // Process restart never restores diagnostic audio.
    }
    public synchronized boolean retain(UtteranceBuffer.Sealed pcm, boolean consent, long now) throws IOException {
        if (!consent || !pcm.purpose.equals("diagnostic") || pcm.isReleased()) return false;
        close();
        bytes = pcm.samples() * 2;
        try (OutputStream out = new FileOutputStream(file)) {
            file.setReadable(false, false); file.setWritable(false, false);
            if (!file.setReadable(true, true) || !file.setWritable(true, true)) throw new IOException("diagnostic_permissions");
            out.write(header(bytes));
            for (int offset = 0; offset < pcm.samples();) {
                byte[] chunk = pcm.chunk(offset, 8192);
                out.write(chunk); offset += chunk.length / 2; Arrays.fill(chunk, (byte) 0);
            }
            retainedAt = now;
        } catch (IOException e) { close(); throw e; }
        finally { pcm.erase(); }
        return true;
    }
    public synchronized boolean available(long now) throws IOException { expire(now); return bytes > 0 && file.isFile(); }
    public synchronized void expire(long now) throws IOException {
        if (bytes > 0 && (now < retainedAt || now - retainedAt >= 300000)) close();
    }
    /** Caller supplies an explicitly chosen destination; exported copies cannot be recalled. */
    public boolean exportWav(OutputStream out, boolean explicitConsent, long now) throws IOException {
        InputStream opened;
        synchronized (this) {
            if (!explicitConsent || !available(now)) return false;
            opened = new FileInputStream(file);
        }
        try (InputStream in = opened) {
            byte[] chunk = new byte[8192];
            try { int n; while ((n = in.read(chunk)) != -1) out.write(chunk, 0, n); }
            finally { Arrays.fill(chunk, (byte) 0); }
        }
        return true;
    }
    /** Descriptor stays open only during local playback, and must close before expire/delete. */
    public synchronized FileInputStream openPlayback(long now) throws IOException {
        return available(now) ? new FileInputStream(file) : null;
    }
    public synchronized void close() throws IOException {
        bytes = 0;
        if (file.exists() && !file.delete()) throw new IOException("diagnostic_delete_failed");
    }
    private static byte[] header(int bytes) {
        ByteBuffer h = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN);
        h.put(new byte[]{'R','I','F','F'}).putInt(bytes + 36).put(new byte[]{'W','A','V','E','f','m','t',' '})
            .putInt(16).putShort((short) 1).putShort((short) 1).putInt(16000).putInt(32000)
            .putShort((short) 2).putShort((short) 16).put(new byte[]{'d','a','t','a'}).putInt(bytes);
        return h.array();
    }
}

import id.my.yhdev.nexo.audio.core.ProcessingDeadline;

/** SDK-free production policy checks, not service scheduling or Android execution. */
public final class ProcessingDeadlineHarness {
    public static void main(String[] args) {
        ProcessingDeadline policy = new ProcessingDeadline();
        policy.begin("conversation");
        assert !policy.complete("conversation", 10) : "Result needs integrity acceptance";
        assert !policy.accepted("stale", 10);
        assert policy.accepted("conversation", 100);
        assert !policy.expired(30099);
        assert policy.accepted("conversation", 30099) : "Duplicate ACK is harmless";
        assert policy.expired(30100) : "Duplicate ACK must not extend the deadline";
        assert !policy.complete("conversation", 30100) : "Late result cannot complete";
        policy.clear();
        assert !policy.expired(Long.MAX_VALUE);
        assert !policy.complete("conversation", 101);
        policy.begin("enrollment");
        assert policy.accepted("enrollment", 1000);
        assert !policy.complete("conversation", 1001);
        assert policy.complete("enrollment", 30999);
        assert !policy.expired(Long.MAX_VALUE) : "Success disarms the timer";
        assert !policy.complete("enrollment", 31000) : "Duplicate result cannot append twice";
        policy.begin("closed");
        assert policy.accepted("closed", 2000);
        policy.clear();
        policy.begin("replacement");
        assert !policy.accepted("closed", 2001);
        assert !policy.complete("closed", 2001);
        assert policy.accepted("replacement", 5000);
        assert policy.expired(4999) : "Clock regression fails closed";
        System.out.println("PASS processing_deadline");
    }
}

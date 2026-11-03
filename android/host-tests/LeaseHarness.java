import id.my.yhdev.nexo.audio.core.CaptureLease;

public final class LeaseHarness {
    private LeaseHarness() {}
    public static void main(String[] args) {
        CaptureLease gate = new CaptureLease();
        String connection = "00000000-0000-4000-8000-000000000010";
        String owner = "00000000-0000-4000-8000-000000000003";
        String lease = "00000000-0000-4000-8000-000000000004";
        long first = gate.open(connection, owner, 0);
        assert !gate.ready(1);
        assert !gate.grant(first, connection, owner, lease, 15000, 1);
        gate.localStopped(first, 2);
        assert !gate.ready(601);
        assert gate.ready(602); // Grant and physical stop + echo tail are both required.
        assert gate.canCapture(first, lease, 603); // No normal microphone intent dependency.
        gate.close();
        assert !gate.ready(604);
        assert !gate.grant(first, connection, owner, lease, 15000, 605);
        long second = gate.open(connection, owner, 1000);
        gate.localStopped(second, 1001);
        gate.grant(second, connection, owner, lease, 15000, 1002);
        assert gate.ready(1601);
        assert gate.renewDue(6002);
        gate.renewSent(second, 6002);
        assert !gate.ready(8002);
        assert !gate.renewed(second, connection, owner, lease, 15000, 8003);
        assert !gate.ready(8004);
        long third = gate.open(connection, owner, 9000);
        assert !gate.grant(third, connection, owner, lease, 15000, 12000);
        assert !gate.ready(12001);
        System.out.println("PASS lease");
    }
}

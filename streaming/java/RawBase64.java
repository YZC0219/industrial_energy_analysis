package industrial.energy.streaming;

import java.util.Base64;
import org.apache.flink.table.functions.ScalarFunction;

/** Keep quarantined Kafka values byte-for-byte recoverable in a JSON sink. */
public final class RawBase64 extends ScalarFunction {
    public String eval(byte[] payload) {
        return payload == null ? null : Base64.getEncoder().encodeToString(payload);
    }
}

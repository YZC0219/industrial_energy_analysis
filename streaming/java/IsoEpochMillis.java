package industrial.energy.streaming;

import java.time.DateTimeException;
import java.time.OffsetDateTime;
import org.apache.flink.table.functions.ScalarFunction;

/** Parse an offset timestamp strictly; null means invalid rather than a coerced date. */
public final class IsoEpochMillis extends ScalarFunction {
    public Long eval(String value) {
        if (value == null) {
            return null;
        }
        try {
            return OffsetDateTime.parse(value).toInstant().toEpochMilli();
        } catch (DateTimeException | ArithmeticException ignored) {
            return null;
        }
    }
}

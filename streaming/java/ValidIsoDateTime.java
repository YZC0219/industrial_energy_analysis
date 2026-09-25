package industrial.energy.streaming;

import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import org.apache.flink.table.functions.ScalarFunction;

/** Accept only a real ISO-8601 timestamp with an explicit UTC offset. */
public final class ValidIsoDateTime extends ScalarFunction {
    public Boolean eval(String value) {
        if (value == null) {
            return false;
        }
        try {
            OffsetDateTime.parse(value);
            return true;
        } catch (DateTimeParseException ignored) {
            return false;
        }
    }
}

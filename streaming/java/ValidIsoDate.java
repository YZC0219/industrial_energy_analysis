package industrial.energy.streaming;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import org.apache.flink.table.functions.ScalarFunction;

/** Reject impossible or non-ISO business dates before typed JSON decoding. */
public final class ValidIsoDate extends ScalarFunction {
    public Boolean eval(String value) {
        if (value == null) {
            return false;
        }
        try {
            LocalDate.parse(value);
            return true;
        } catch (DateTimeParseException ignored) {
            return false;
        }
    }
}

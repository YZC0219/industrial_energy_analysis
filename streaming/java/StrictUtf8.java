package industrial.energy.streaming;

import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import org.apache.flink.table.functions.ScalarFunction;

/** Reject malformed Kafka value bytes without ever replacing invalid UTF-8. */
public final class StrictUtf8 extends ScalarFunction {
    public Boolean eval(byte[] payload) {
        if (payload == null) {
            return false;
        }
        try {
            StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(payload));
            return true;
        } catch (CharacterCodingException ignored) {
            return false;
        }
    }
}

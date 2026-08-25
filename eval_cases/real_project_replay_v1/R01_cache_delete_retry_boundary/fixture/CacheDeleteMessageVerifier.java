import java.time.Duration;
import java.time.LocalDateTime;

public final class CacheDeleteMessageVerifier {
    public static void main(String[] args) {
        long[] expected = {1, 5, 30, 300, 1800};
        CacheDeleteMessage message = new CacheDeleteMessage();
        for (int i = 0; i < expected.length; i++) {
            LocalDateTime before = LocalDateTime.now();
            message.incrementRetry();
            long actual = Duration.between(before, message.getNextExecuteTime()).toMillis();
            long target = expected[i] * 1000;
            if (actual < target - 250 || actual > target + 1000) {
                throw new AssertionError("retry " + (i + 1) + " scheduled " + actual + "ms");
            }
            if (message.getRetryCount() != i + 1) {
                throw new AssertionError("retry count did not increment exactly once");
            }
        }
        if (!message.isExhausted() || message.getNextDelaySeconds() != -1) {
            throw new AssertionError("message must be exhausted after retry five");
        }
    }
}

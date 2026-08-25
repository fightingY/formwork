/** Dependency-free extraction of the cache deletion retry policy. */
public final class RetryPolicy {
    public static final int MAX_RETRY_COUNT = 5;
    private static final long[] RETRY_DELAYS = {1, 5, 30, 300, 1800};

    private RetryPolicy() {
    }

    public static long delayForRetryCount(int retryCount) {
        if (retryCount < 0) {
            throw new IllegalArgumentException("retryCount must not be negative");
        }
        if (retryCount >= MAX_RETRY_COUNT) {
            return -1;
        }
        return RETRY_DELAYS[retryCount];
    }
}

import java.time.LocalDateTime;

/** Minimal dependency-free extraction of MyHeiMaDianPing's cache deletion message. */
public final class CacheDeleteMessage {
    public static final int MAX_RETRY_COUNT = 5;
    public static final long[] RETRY_DELAYS = {1, 5, 30, 300, 1800};

    private int retryCount;
    private LocalDateTime nextExecuteTime;

    public CacheDeleteMessage() {
        this.retryCount = 0;
        this.nextExecuteTime = LocalDateTime.now();
    }

    public int getRetryCount() {
        return retryCount;
    }

    public LocalDateTime getNextExecuteTime() {
        return nextExecuteTime;
    }

    public long getNextDelaySeconds() {
        if (retryCount >= MAX_RETRY_COUNT) {
            return -1;
        }
        return RETRY_DELAYS[retryCount];
    }

    public void incrementRetry() {
        retryCount++;
        long delaySeconds = getNextDelaySeconds();
        if (delaySeconds > 0) {
            nextExecuteTime = LocalDateTime.now().plusSeconds(delaySeconds);
        }
    }

    public boolean isExhausted() {
        return retryCount >= MAX_RETRY_COUNT;
    }
}

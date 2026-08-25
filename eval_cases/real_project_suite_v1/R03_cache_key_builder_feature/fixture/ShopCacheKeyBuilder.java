import java.util.Locale;

/** Small cache-key component extracted from MyHeiMaDianPing's RedisConstants usage. */
public final class ShopCacheKeyBuilder {
    private static final String SHOP_PREFIX = "cache:shop:";
    private static final String SEARCH_PREFIX = "cache:shop:search:";

    private ShopCacheKeyBuilder() {
    }

    public static String shopKey(long shopId) {
        return SHOP_PREFIX + shopId;
    }

    public static String searchKey(String keyword, long typeId, int page) {
        return SEARCH_PREFIX + typeId + ":" + keyword.toLowerCase(Locale.ROOT) + ":p" + page;
    }
}

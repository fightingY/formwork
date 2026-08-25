import java.util.Locale;

public final class ShopCacheKeyBuilderVerifier {
    private static void equals(String expected, String actual) {
        if (!expected.equals(actual)) throw new AssertionError(expected + " != " + actual);
    }
    private static void rejects(Runnable operation) {
        try { operation.run(); } catch (IllegalArgumentException expected) { return; }
        throw new AssertionError("expected IllegalArgumentException");
    }
    public static void main(String[] args) {
        Locale original = Locale.getDefault();
        try {
            Locale.setDefault(Locale.forLanguageTag("tr-TR"));
            equals("cache:shop:42", ShopCacheKeyBuilder.shopKey(42));
            equals("cache:shop:search:7:hot-pot:p3", ShopCacheKeyBuilder.searchKey("  HOT   Pot  ", 7, 3));
            equals("cache:shop:search:2:izakaya:p1", ShopCacheKeyBuilder.searchKey("IZAKAYA", 2, 1));
            rejects(() -> ShopCacheKeyBuilder.searchKey(null, 1, 1));
            rejects(() -> ShopCacheKeyBuilder.searchKey("   ", 1, 1));
            rejects(() -> ShopCacheKeyBuilder.searchKey("food", 0, 1));
            rejects(() -> ShopCacheKeyBuilder.searchKey("food", 1, 0));
        } finally { Locale.setDefault(original); }
    }
}

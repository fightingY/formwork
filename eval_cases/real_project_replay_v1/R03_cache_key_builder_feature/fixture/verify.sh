#!/bin/sh
set -eu
rm -rf build
mkdir build
javac -encoding UTF-8 -d build ShopCacheKeyBuilder.java ShopCacheKeyBuilderVerifier.java
java -cp build ShopCacheKeyBuilderVerifier

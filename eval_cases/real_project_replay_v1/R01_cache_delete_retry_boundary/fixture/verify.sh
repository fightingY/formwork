#!/bin/sh
set -eu
rm -rf build
mkdir build
javac -encoding UTF-8 -d build CacheDeleteMessage.java CacheDeleteMessageVerifier.java
java -cp build CacheDeleteMessageVerifier

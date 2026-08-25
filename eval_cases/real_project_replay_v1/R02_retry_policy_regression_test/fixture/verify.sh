#!/bin/sh
set -eu
rm -rf build build-mutant
mkdir build build-mutant
javac -encoding UTF-8 -d build RetryPolicy.java RetryPolicyBoundaryTest.java
java -cp build RetryPolicyBoundaryTest
sed 's/retryCount >= MAX_RETRY_COUNT/retryCount > MAX_RETRY_COUNT/' RetryPolicy.java > build-mutant/RetryPolicy.java
cp RetryPolicyBoundaryTest.java build-mutant/
javac -encoding UTF-8 -d build-mutant build-mutant/RetryPolicy.java build-mutant/RetryPolicyBoundaryTest.java
if java -cp build-mutant RetryPolicyBoundaryTest; then
  echo "max-boundary mutant was not detected" >&2
  exit 1
fi
sed 's/new IllegalArgumentException/new IllegalStateException/' RetryPolicy.java > build-mutant/RetryPolicy.java
javac -encoding UTF-8 -d build-mutant build-mutant/RetryPolicy.java build-mutant/RetryPolicyBoundaryTest.java
if java -cp build-mutant RetryPolicyBoundaryTest; then
  echo "exception-contract mutant was not detected" >&2
  exit 1
fi

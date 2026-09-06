#!/usr/bin/env bash
# validate_patch.sh red|green|deploy|revert — red/green validation of a hugegraph-core patch on the server node
#   (clone ~/hugegraph, working tree = branch under test). PATCH=<file> selects the patch (default: ~/paging-batch-boundary.patch).
#   red   : apply patch, reset hugegraph-core -> the new tests run against UNPATCHED core => must FAIL
#   green : apply the core part -> same tests => must PASS
#   deploy: package core, copy the jar into lib/ of both distributions (backup .orig), restart servers without wiping data
#   revert: restore the .orig jars, git checkout -- .
set -uo pipefail
H=$HOME; REPO=$H/hugegraph; PATCH=${PATCH:-$H/paging-batch-boundary.patch}
SV=$REPO/hugegraph-server/apache-hugegraph-server-1.7.0
RD=$REPO/hugegraph-server/dist-rocksdb
export JAVA_HOME=$H/tools/jdk17 PATH=$H/tools/jdk17/bin:$H/tools/apache-maven-3.9.9/bin:/usr/bin:/bin:/usr/sbin
MVN="mvn -q -ntp -Drat.skip=true -Dcheckstyle.skip=true -Dmaven.javadoc.skip=true"
TESTS='EdgeCoreTest#testQueryOutEdgesOfVertexInPagingAtBatchBoundary,VertexCoreTest#testQueryVerticesByLabelInPagingAtBatchBoundary'
run_tests() {   # $1 = label
  (cd $REPO && $MVN -pl hugegraph-server/hugegraph-test -am -P core-test,rocksdb -Dtest="$TESTS" -DfailIfNoTests=false -Dsurefire.failIfNoSpecifiedTests=false test) > /tmp/fixtest_$1.log 2>&1
  echo "  [$1] mvn exit=$?"
  grep -E "Tests run:|FAIL|limit [0-9]+ expected|AssertionError" /tmp/fixtest_$1.log | grep -v "^\[INFO\] *$" | head -12 | cut -c1-160
}
case ${1:?red|green|deploy|revert} in
  red)
    cd $REPO && (git apply --check $PATCH 2>/dev/null && git apply $PATCH)
    git checkout -- hugegraph-server/hugegraph-core   # red = new tests, core UNPATCHED
    echo "  patch state: $(git diff --stat | tail -1)"
    run_tests red
    ;;
  green)
    cd $REPO && git apply --include='*BinaryEntryIterator.java' $PATCH; echo "  patch state: $(git diff --stat | tail -1)"
    run_tests green
    ls -la $REPO/hugegraph-server/hugegraph-core/target/hugegraph-core-1.7.0.jar
    ;;
  deploy)
    (cd $REPO && $MVN -pl hugegraph-server/hugegraph-core -am -DskipTests package) > /tmp/fixcore_pkg.log 2>&1; echo "  core package exit=$?"
    J=$REPO/hugegraph-server/hugegraph-core/target/hugegraph-core-1.7.0.jar; ls -la $J
    for D in $SV $RD; do [ -f $D/lib/hugegraph-core-1.7.0.jar.orig ] || cp $D/lib/hugegraph-core-1.7.0.jar $D/lib/hugegraph-core-1.7.0.jar.orig; cp $J $D/lib/hugegraph-core-1.7.0.jar; done
    export JAVA_HOME=$H/tools/jdk11 PATH=$H/tools/jdk11/bin:/usr/bin:/bin:/usr/sbin
    for D in $SV $RD; do (cd $D && ./bin/stop-hugegraph.sh) >/dev/null 2>&1; done; sleep 3
    for p in 8080 8081 8182 8183; do P=$(ss -tlnp 2>/dev/null | grep ":$p " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2); [ -n "$P" ] && kill -9 $P; done; sleep 2
    for D in $SV $RD; do rm -f $D/bin/pid; (cd $D && ./bin/start-hugegraph.sh) >/dev/null 2>&1; done
    for i in $(seq 1 30); do curl -sf http://127.0.0.1:8080/graphspaces/DEFAULT/graphs/hugegraph/schema/propertykeys >/dev/null && curl -sf http://127.0.0.1:8081/graphspaces/DEFAULT/graphs/hugegraph/schema/propertykeys >/dev/null && break; sleep 3; done
    echo "  servers restarted with patched core: 8080=$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8080/graphspaces/DEFAULT/graphs/hugegraph) 8081=$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8081/graphspaces/DEFAULT/graphs/hugegraph)"
    ;;
  revert)
    for D in $SV $RD; do [ -f $D/lib/hugegraph-core-1.7.0.jar.orig ] && mv $D/lib/hugegraph-core-1.7.0.jar.orig $D/lib/hugegraph-core-1.7.0.jar; done
    cd $REPO && git checkout -- . && echo "  reverted"
    ;;
esac

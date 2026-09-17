# Property codec mismatch, server vs struct (issue #3090)

`PropertyCodecMismatch.java`: writes one value per data type with the server
`BytesBuffer.writeProperty(PropertyKey, value)` (hugegraph-core) and reads the
bytes back with the struct `BytesBuffer.readProperty(PropertyKey)` (hugegraph-struct),
which is what the store-side `FilterIterator` does for every pushed-down query.

Run on master `1a15e762` (JDK 21) with the server dist lib plus the struct jar:

    CP=<struct jar>:<server dist>/lib/*
    javac -proc:none -cp "$CP" PropertyCodecMismatch.java && java -cp "$CP:." PropertyCodecMismatch

Output: `master-1a15e762.txt`. Every type throws; the exception depends only on
the first byte of the value. The last line is the round trip of a server
`ConditionQuery` through the struct `ConditionQuery.fromBytes` (resultType and
conditions survive, so the store would parse rows if the bytes reached it).

import org.apache.hugegraph.backend.id.IdGenerator;
import org.apache.hugegraph.type.define.DataType;
import org.apache.hugegraph.type.define.Cardinality;
public class PropertyCodecMismatch { public static void main(String[] a) throws Exception {
  Object[][] cases = {{DataType.DOUBLE,1.5},{DataType.DOUBLE,63.5},{DataType.LONG,100L},{DataType.TEXT,"ETC"},{DataType.INT,7},{DataType.BOOLEAN,true},{DataType.FLOAT,0.5f},{DataType.DATE,new java.util.Date(0)}};
  for (Object[] c : cases) {
    org.apache.hugegraph.schema.PropertyKey spk = new org.apache.hugegraph.schema.PropertyKey(null, IdGenerator.of(1L), "p");
    spk.dataType((DataType) c[0]); spk.cardinality(Cardinality.SINGLE);
    org.apache.hugegraph.backend.serializer.BytesBuffer sb = org.apache.hugegraph.backend.serializer.BytesBuffer.allocate(64);
    sb.writeProperty(spk, c[1]); byte[] bytes = sb.bytes();
    StringBuilder hex = new StringBuilder(); for (byte b : bytes) hex.append(String.format("%02x", b));
    org.apache.hugegraph.struct.schema.PropertyKey tpk = new org.apache.hugegraph.struct.schema.PropertyKey(null, org.apache.hugegraph.id.IdGenerator.of(1L), "");
    String res;
    try { Object v = org.apache.hugegraph.serializer.BytesBuffer.wrap(bytes).readProperty(tpk); res = "OK -> " + v + " (decoded as " + tpk.cardinality() + "/" + tpk.dataType() + ")"; }
    catch (Throwable t) { res = "THROW " + t.getClass().getSimpleName() + ": " + t.getMessage(); }
    System.out.println(c[0] + " " + c[1] + " server-bytes=" + hex + " -> struct.readProperty: " + res);
  }
}}

import org.apache.hugegraph.pd.client.KvClient;
import org.apache.hugegraph.pd.client.PDConfig;
import org.apache.hugegraph.pd.grpc.kv.ScanPrefixResponse;
import java.util.*;
public class PdKeys { public static void main(String[] a) throws Exception {
  PDConfig c = PDConfig.of(a[0]); c.setEnableCache(false);
  KvClient<?> k = new KvClient<>(c);
  ScanPrefixResponse r = k.scanPrefix(a.length > 1 ? a[1] : "HUGEGRAPH/");
  Map<String,String> m = r.getKvsMap(); System.out.println("keys: " + m.size());
  TreeMap<String,Integer> byPrefix = new TreeMap<>();
  for (String key : m.keySet()) { String[] p = key.split("/"); String pre = p.length > 4 ? String.join("/", Arrays.copyOfRange(p, 0, 5)) : key; byPrefix.merge(pre, 1, Integer::sum); }
  byPrefix.forEach((p, n) -> System.out.println("  " + n + "  " + p));
  for (String key : new TreeSet<>(m.keySet())) if (key.contains("PROPERTY_KEY") || key.contains("GRAPH_CONF") || key.contains("/GRAPH/")) { System.out.println("    " + key + " = " + m.get(key).replaceAll("\\s+"," ").substring(0, Math.min(90, m.get(key).length()))); }
}}

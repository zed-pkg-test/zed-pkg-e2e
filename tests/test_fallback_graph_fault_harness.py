import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from fallback_graph_fault_harness import run_fault_graph, Singleflight, DeadlineExceeded
class FaultGraph(unittest.TestCase):
  def test_same_identity_fails_once_per_operation_under_parallel_load(self):
    x=run_fault_graph()
    self.assertEqual(x["fixture_count"],72)
    self.assertEqual(x["requests"],{"shared-a":1,"shared-b":1,"fail-primary":1,"fail-secondary":1})
    self.assertEqual(x["unique_artifacts"],2)
    self.assertEqual(x["successes"],36)
  def test_elapsed_deadline_is_fail_closed_before_network(self):
    import time
    resolver=Singleflight("http://127.0.0.1:1",deadline_seconds=-1)
    with self.assertRaises(DeadlineExceeded):resolver.fetch("shared-a")
if __name__=="__main__":unittest.main()

import unittest

from tools.gitlab_demo.common import DemoError
from tools.gitlab_demo.monitoring import METRICS, query_window, sample_count, selected_metrics


class MonitoringTests(unittest.TestCase):
    def test_zero_is_a_real_sample_but_null_and_definitions_are_not(self):
        self.assertEqual(sample_count({"value": [{"timeseries": [{"data": [{"average": 0}]}]}]}), 1)
        self.assertEqual(sample_count({"value": [{"timeseries": [{"data": [{"average": None}]}]}]}), 0)
        self.assertEqual(sample_count({"metrics": {"CpuPercentage": {"name": "CpuPercentage"}}}), 0)

    def test_staging_definitions_use_actual_slot_not_production_parent(self):
        resource = {"id": "/sites/demo/slots/staging", "type": "Microsoft.Web/sites/slots"}
        definitions = [{
            "id": f"/sites/demo/slots/staging/definitions/{name}",
            "name": {"value": name}, "namespace": "Microsoft.Web/sites/slots",
            "supportedAggregationTypes": [aggregation], "unit": "Count",
        } for name, aggregation in METRICS["staging"].items()]
        selected = selected_metrics(definitions, resource, "staging")
        self.assertEqual(len(selected), 4)
        self.assertTrue(all(item["resourceId"].endswith("/slots/staging") for item in selected.values()))
        with self.assertRaises(DemoError):
            selected_metrics([], resource, "staging")

    def test_metric_window_is_explicitly_broader_than_actual_run(self):
        start, end = query_window({
            "startDateTime": "2026-10-19T13:29:50Z", "endDateTime": "2026-10-19T13:31:19Z",
        })
        self.assertEqual(start, "2026-10-19T13:28:00+00:00")
        self.assertEqual(end, "2026-10-19T13:33:00+00:00")


if __name__ == "__main__":
    unittest.main()

"""Unit tests for the chart stats log entry parser."""

import json
import unittest

from app.pollers.charts import parse_log_entries


class TestParseLogEntries(unittest.TestCase):
    """Tests for parse_log_entries()."""

    def test_empty_string(self):
        self.assertEqual(parse_log_entries("", node_id=1), [])

    def test_single_entry_user_only(self):
        line = "1775573477,ut:8078:33,ur:6719:41"
        rows = parse_log_entries(line, node_id=5)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["node_id"], 5)
        self.assertEqual(row["timestamp"], 1775573477)
        self.assertEqual(row["user_tx_bytes"], 8078)
        self.assertEqual(row["user_tx_pkts"], 33)
        self.assertEqual(row["user_rx_bytes"], 6719)
        self.assertEqual(row["user_rx_pkts"], 41)
        self.assertIsNone(row["channel_data"])
        self.assertIsNone(row["tunnel_data"])

    def test_channel_data(self):
        line = "1775573477,c0t:4487,c0r:4016,c1t:148,c1r:156,ut:100:10,ur:200:20"
        rows = parse_log_entries(line, node_id=1)
        self.assertEqual(len(rows), 1)
        channels = json.loads(rows[0]["channel_data"])
        self.assertEqual(len(channels), 2)
        self.assertEqual(channels[0], {"ch": 0, "tx": 4487, "rx": 4016})
        self.assertEqual(channels[1], {"ch": 1, "tx": 148, "rx": 156})

    def test_tunnel_data(self):
        line = "1775573477,s1_0t:4359,s1_0r:3812,s1_0d:50489,ut:100:10,ur:200:20"
        rows = parse_log_entries(line, node_id=1)
        self.assertEqual(len(rows), 1)
        tunnels = json.loads(rows[0]["tunnel_data"])
        self.assertEqual(len(tunnels), 1)
        self.assertEqual(tunnels[0], {"site": 1, "tunnel": 0, "tx": 4359, "rx": 3812, "delay_us": 50489})

    def test_multiple_entries(self):
        lines = "1775573477,ut:100:10,ur:200:20\n1775573478,ut:110:11,ur:210:21\n"
        rows = parse_log_entries(lines, node_id=1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["timestamp"], 1775573477)
        self.assertEqual(rows[1]["timestamp"], 1775573478)

    def test_full_entry(self):
        line = "1775573477,c0t:4487,c0r:4016,c1t:148,c1r:156,s1_0t:4359,s1_0r:3812,s1_0d:50489,ut:8078:33,ur:6719:41"
        rows = parse_log_entries(line, node_id=2)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["user_tx_bytes"], 8078)
        self.assertEqual(row["user_rx_bytes"], 6719)
        channels = json.loads(row["channel_data"])
        tunnels = json.loads(row["tunnel_data"])
        self.assertEqual(len(channels), 2)
        self.assertEqual(len(tunnels), 1)

    def test_blank_lines_skipped(self):
        lines = "\n\n1775573477,ut:100:10,ur:200:20\n\n"
        rows = parse_log_entries(lines, node_id=1)
        self.assertEqual(len(rows), 1)

    def test_invalid_timestamp_skipped(self):
        lines = "badts,ut:100:10,ur:200:20\n1775573477,ut:100:10,ur:200:20"
        rows = parse_log_entries(lines, node_id=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["timestamp"], 1775573477)


if __name__ == "__main__":
    unittest.main()

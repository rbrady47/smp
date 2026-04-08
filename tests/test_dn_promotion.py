"""Unit tests for DN promotion schema and logic."""

import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite:///smp-test.db")

from pydantic import ValidationError

from app.schemas import DnPromoteRequest


class TestDnPromoteRequest(unittest.TestCase):
    """Tests for the DnPromoteRequest Pydantic schema."""

    def test_minimal_valid_payload(self):
        req = DnPromoteRequest(api_username="admin", api_password="secret")
        self.assertEqual(req.api_username, "admin")
        self.assertEqual(req.api_password, "secret")
        self.assertEqual(req.web_port, 443)
        self.assertEqual(req.ssh_port, 22)
        self.assertTrue(req.ping_enabled)
        self.assertTrue(req.charts_enabled)
        self.assertTrue(req.include_in_topology)
        self.assertFalse(req.api_use_https)
        self.assertIsNone(req.name)
        self.assertIsNone(req.host)
        self.assertIsNone(req.notes)

    def test_full_payload(self):
        req = DnPromoteRequest(
            name="Site Alpha",
            host="10.0.0.5",
            location="Bldg 42",
            web_port=8443,
            ssh_port=2222,
            api_username="operator",
            api_password="p@ssw0rd",
            api_use_https=True,
            include_in_topology=False,
            topology_level=0,
            topology_unit="1BCT",
            ping_enabled=False,
            ping_interval_seconds=30,
            charts_enabled=False,
            notes="Promoted from DN",
        )
        self.assertEqual(req.name, "Site Alpha")
        self.assertEqual(req.host, "10.0.0.5")
        self.assertEqual(req.web_port, 8443)
        self.assertEqual(req.topology_unit, "1BCT")
        self.assertFalse(req.charts_enabled)
        self.assertEqual(req.ping_interval_seconds, 30)

    def test_missing_api_username_fails(self):
        with self.assertRaises(ValidationError):
            DnPromoteRequest(api_password="secret")

    def test_missing_api_password_fails(self):
        with self.assertRaises(ValidationError):
            DnPromoteRequest(api_username="admin")

    def test_empty_api_username_fails(self):
        with self.assertRaises(ValidationError):
            DnPromoteRequest(api_username="", api_password="secret")

    def test_extra_fields_rejected(self):
        with self.assertRaises(ValidationError):
            DnPromoteRequest(
                api_username="admin",
                api_password="secret",
                bogus_field="nope",
            )

    def test_invalid_topology_unit_rejected(self):
        with self.assertRaises(ValidationError):
            DnPromoteRequest(
                api_username="admin",
                api_password="secret",
                topology_unit="INVALID",
            )

    def test_port_range_validation(self):
        with self.assertRaises(ValidationError):
            DnPromoteRequest(
                api_username="admin",
                api_password="secret",
                web_port=0,
            )
        with self.assertRaises(ValidationError):
            DnPromoteRequest(
                api_username="admin",
                api_password="secret",
                ssh_port=99999,
            )


if __name__ == "__main__":
    unittest.main()

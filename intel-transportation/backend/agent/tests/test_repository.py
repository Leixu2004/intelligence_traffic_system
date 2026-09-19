import unittest
from unittest.mock import MagicMock, patch

from backend.agent.repository import TrafficReadRepository


class TrafficRepositoryTests(unittest.TestCase):
    def test_missing_dsn_returns_explicit_unavailable_source(self):
        records, source, warning = TrafficReadRepository("").traffic_records()
        self.assertEqual(records, [])
        self.assertEqual(source, "unavailable")
        self.assertIn("TIMESCALEDB_DSN", warning)

    @patch("backend.agent.repository.psycopg2.connect")
    def test_queries_use_read_only_session_timeout_and_limit(self, connect):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        connect.return_value = connection

        TrafficReadRepository("dbname=traffic").traffic_records("CP-1", 50)

        connect.assert_called_once_with("dbname=traffic", connect_timeout=3)
        connection.set_session.assert_called_once_with(readonly=True, autocommit=True)
        self.assertTrue(
            cursor.execute.call_args_list[0].args[0].startswith("SELECT set_config")
        )
        self.assertIn("LIMIT %s", cursor.execute.call_args_list[1].args[0])
        self.assertEqual(cursor.execute.call_args_list[1].args[1], ("CP-1", 50))


if __name__ == "__main__":
    unittest.main()

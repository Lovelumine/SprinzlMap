from __future__ import annotations

import os
import unittest
from unittest import mock

import pymysql

from backend.infra.config.mysql_settings import load_mysql_config


class MysqlSettingsTest(unittest.TestCase):
    def test_load_mysql_config_requires_core_fields(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(load_mysql_config())

    def test_load_mysql_config_parses_values(self) -> None:
        env = {
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_PORT": "3307",
            "MYSQL_USER": "tester",
            "MYSQL_PASSWORD": "secret",
            "MYSQL_DATABASE": "demo",
            "MYSQL_CHARSET": "utf8",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = load_mysql_config()
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config["host"], "127.0.0.1")
        self.assertEqual(config["port"], 3307)
        self.assertEqual(config["charset"], "utf8")
        self.assertIs(config["cursorclass"], pymysql.cursors.DictCursor)

    def test_load_mysql_config_clamps_timeouts(self) -> None:
        env = {
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_USER": "tester",
            "MYSQL_PASSWORD": "secret",
            "MYSQL_DATABASE": "demo",
            "MYSQL_CONNECT_TIMEOUT": "0",
            "MYSQL_READ_TIMEOUT": "999",
            "MYSQL_WRITE_TIMEOUT": "4",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = load_mysql_config(include_timeouts=True)
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config["connect_timeout"], 1)
        self.assertEqual(config["read_timeout"], 120)
        self.assertEqual(config["write_timeout"], 4)


if __name__ == "__main__":
    unittest.main()

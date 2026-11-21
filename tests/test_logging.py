from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autozhu.config import LogConfig
from autozhu.logging_setup import setup_logging


def close_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()


class LoggingTest(unittest.TestCase):
    def tearDown(self) -> None:
        close_handlers()

    def test_writes_rotating_file_and_creates_parent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "auto-zhu.log"
            setup_logging(LogConfig(level="INFO", file=str(target)))
            logging.getLogger("autozhu.test").info("hello 日志")
            for handler in logging.getLogger().handlers:
                handler.flush()
            self.assertTrue(target.exists())
            self.assertIn("hello 日志", target.read_text(encoding="utf-8"))
            close_handlers()

    def test_works_without_console(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(sys, "stdout", None):
                setup_logging(LogConfig(level="INFO", file=str(Path(tmp) / "a.log")))
                logging.getLogger("autozhu.test").warning("no console")
                for handler in logging.getLogger().handlers:
                    handler.flush()
                self.assertTrue((Path(tmp) / "a.log").exists())
                close_handlers()

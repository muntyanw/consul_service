"""Tests.py
~~~~~~~~~~

Single test-suite covering core utility modules.  Designed to run with *pytest*
(or standard ``python Tests.py`` via unittest main).  Heavy GUI interactions are
mocked so CI can execute without a display.

> Note: only key logic is asserted; full integration with PyAutoGUI/OpenCV is
> verified manually because it depends on real screen output.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from cryptography.fernet import Fernet

# ---------------------------------------------------------------------------
# Ensure project modules are in path for direct execution
# ---------------------------------------------------------------------------
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from io.yaml_loader import YAMLLoader, ConfigError
from io.config_watcher import ConfigWatcher, ChangeKind
from utils.crypto_utils import encrypt, decrypt, generate_key
from utils.profile_manager import prepare as prepare_profile


class CryptoUtilTests(unittest.TestCase):
    def setUp(self):
        self.key = generate_key()
        os.environ["FERNET_SECRET_KEY"] = self.key

    def tearDown(self):
        os.environ.pop("FERNET_SECRET_KEY", None)

    def test_encrypt_decrypt_roundtrip(self):
        token = encrypt("secret123")
        self.assertEqual(decrypt(token), "secret123")


class YAMLLoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.keys = self.tmp / "keys"
        self.keys.mkdir()
        self.key_file = self.keys / "demo.dat"
        self.key_file.write_text("dummy")

        os.environ["FERNET_SECRET_KEY"] = generate_key()
        enc_pass = encrypt("p@ss")

        (self.tmp / "user1.yaml").write_text(
            dedent(
                f"""
                key_path: "{self.key_file}"
                key_password: "{enc_pass}"
                birthdate: "1990-01-01"
                gender: "Male"
                country: "Canada"
                consulates: ["Toronto"]
                service: "Passport"
                """
            )
        )

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("FERNET_SECRET_KEY", None)

    def test_loader_success(self):
        loader = YAMLLoader(self.tmp, self.keys)
        cfgs = loader.load()
        self.assertEqual(len(cfgs), 1)
        self.assertEqual(cfgs[0].country, "Canada")

    def test_loader_missing_field(self):
        (self.tmp / "bad.yaml").write_text("{}")
        loader = YAMLLoader(self.tmp, self.keys)
        with self.assertRaises(ConfigError):
            loader.load()


class ConfigWatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.events: list[str] = []

    def tearDown(self):
        import shutil, time

        time.sleep(0.1)  # allow watchdog to finish
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_modify_delete(self):
        from io.config_watcher import ConfigWatcher
        from io.config_watcher import ChangeEvent

        def on_evt(evt: ChangeEvent):
            self.events.append(evt.kind.name)

        watcher = ConfigWatcher(self.tmp, on_evt, poll_idle=0.05)
        watcher.start()

        f = self.tmp / "file.yaml"
        f.write_text("a: 1")  # CREATE
        f.write_text("a: 2")  # MODIFY
        f.unlink()  # DELETE

        import time

        time.sleep(0.3)
        watcher.close()
        self.assertEqual(self.events[:3], ["CREATED", "MODIFIED", "DELETED"])


class ProfileManagerTests(unittest.TestCase):
    def setUp(self):
        # Make fake template dir
        self.template = Path(tempfile.mkdtemp())
        (self.template / "First Run").touch()
        # Patch settings.yaml
        settings = PROJECT_ROOT / "settings.yaml"
        settings.write_text(f"chrome_template: '{self.template}'\n")

    def test_prepare_cleanup(self):
        from utils.profile_manager import prepare as prepare_profile

        with prepare_profile("testuser") as prof:
            self.assertTrue(prof.exists())
            self.assertTrue((prof / "First Run").exists())
        # after context – directory removed
        self.assertFalse(prof.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

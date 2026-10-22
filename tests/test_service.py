"""Check the opt-in service package without a user manager or live provider."""

import configparser
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
UNIT = ROOT / "packaging/palka-provider.service"


class ServicePackageTests(unittest.TestCase):
    def setUp(self):
        self.unit = configparser.ConfigParser(interpolation=None)
        self.unit.read(UNIT)

    def test_absolute_exec_and_external_credentials(self):
        service = self.unit["Service"]
        command = service["ExecStart"].replace("%h", "/synthetic/home")
        executable, orb, provider, dispatch = command.split()
        self.assertTrue(Path(executable).is_absolute())
        self.assertEqual((orb, provider), ("orb", "provider"))
        self.assertTrue(Path(dispatch).is_absolute())
        self.assertEqual(service["EnvironmentFile"], "%h/.config/palka/provider.env")
        self.assertEqual(service["UMask"], "0077")
        self.assertNotIn("TOKEN", UNIT.read_text())

    def test_bounded_failure_and_stop_policy(self):
        service = self.unit["Service"]
        self.assertEqual(service["Restart"], "on-failure")
        self.assertEqual(int(service["RestartSec"]), 30)
        self.assertEqual(int(self.unit["Unit"]["StartLimitBurst"]), 3)
        self.assertEqual(int(self.unit["Unit"]["StartLimitIntervalSec"]), 300)
        self.assertEqual(int(service["TimeoutStopSec"]), 270)
        self.assertEqual(service["KillMode"], "control-group")
        self.assertEqual(service["SendSIGKILL"], "yes")
        self.assertNotIn("Install", self.unit)
        self.assertEqual(
            [key for key in service if key.startswith("exec")], ["execstart"]
        )

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze required")
    def test_systemd_verify_without_activation(self):
        # Resolve home paths to a disposable fixture. Never execute the unit.
        with tempfile.TemporaryDirectory(prefix="palka-service-") as directory:
            home = Path(directory)
            amp = home / ".local/bin/amp"
            amp.parent.mkdir(parents=True)
            amp.write_text("#!/bin/sh\nexit 0\n")
            amp.chmod(0o700)
            (home / ".local/lib/palka").mkdir(parents=True)
            env = home / ".config/palka/provider.env"
            env.parent.mkdir(parents=True, mode=0o700)
            env.write_text("AMP_ORB_PROVIDER_TOKEN=synthetic-service-marker\n")
            env.chmod(0o600)
            unit = home / UNIT.name
            unit.write_text(UNIT.read_text().replace("%h", str(home)))
            result = subprocess.run(
                ["systemd-analyze", "--user", "verify", "--man=no", str(unit)],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("synthetic-service-marker", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

"""Exercise the dispatch contract against disposable real Docker containers."""

import json
import os
from pathlib import Path
import subprocess
import time
import unittest
import uuid


ROOT = Path(__file__).resolve().parent.parent


class DockerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image = "palka-test:" + uuid.uuid4().hex
        subprocess.run(
            ["docker", "build", "--tag", cls.image, str(ROOT / "tests/fixtures")],
            check=True,
            stdout=subprocess.DEVNULL,
            timeout=240,
        )
        cls.addClassCleanup(
            subprocess.run,
            ["docker", "image", "rm", cls.image],
            check=True,
            stdout=subprocess.DEVNULL,
            timeout=30,
        )

    def setUp(self):
        self.orb_id = "test.orb_" + uuid.uuid4().hex
        self.container = "palka-" + self.orb_id
        self.addCleanup(
            subprocess.run,
            ["docker", "rm", "--force", "--volumes", self.container],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )

    def docker(self, *args):
        return subprocess.check_output(["docker", *args], text=True, timeout=30).strip()

    def dispatch(self, operation, token="synthetic-first-token", amp_url="https://amp.invalid", **job):
        env = {key: value for key, value in os.environ.items() if not key.startswith("ORB_") and key != "AMP_URL"}
        env.update(ORB_OPERATION=operation, ORB_ID=self.orb_id, ORB_IMAGE=self.image)
        env["AMP_ORB_PROVIDER_TOKEN"] = "synthetic-provider-token"
        if token is not None:
            env["ORB_TOKEN"] = token
        if amp_url is not None:
            env["AMP_URL"] = amp_url
        env.update(job)
        result = subprocess.run(
            [str(ROOT / "dispatch.py")],
            env=env,
            text=True,
            capture_output=True,
            timeout=260,
        )
        self.assertNotIn(token or "synthetic-first-token", result.stdout + result.stderr)
        self.assertNotIn("synthetic-provider-token", result.stdout + result.stderr)
        return result

    def record(self, token):
        for _ in range(100):
            result = subprocess.run(
                ["docker", "exec", self.container, "cat", "/tmp/bootstrap-record"],
                text=True,
                capture_output=True,
                timeout=10,
            )
            lines = result.stdout.splitlines()
            if len(lines) == 4 and lines[1] == token:
                return lines
            time.sleep(0.05)
        self.fail("Synthetic bootstrap did not receive the current credentials.")

    def test_disk_survives_pause_and_resume_with_current_credentials(self):
        result = self.dispatch("spawn", ORB_SNAPSHOT_ID="synthetic-snapshot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("snapshots are not supported", result.stderr)
        first = self.record("synthetic-first-token")
        self.assertEqual(first[:3], ["user", "synthetic-first-token", "https://amp.invalid"])
        container_id = self.docker("inspect", "--format", "{{.Id}}", self.container)
        self.docker("exec", self.container, "bash", "-c", "printf sentinel > /home/user/sentinel")

        self.assertEqual(self.dispatch("pause", token=None).returncode, 0)
        self.assertEqual(self.docker("inspect", "--format", "{{.State.Running}}", self.container), "false")
        self.assertEqual(self.dispatch("pause", token=None).returncode, 0)
        result = self.dispatch("spawn", token="synthetic-second-token", amp_url=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record("synthetic-second-token")[:3], ["user", "synthetic-second-token", ""])
        self.assertEqual(self.docker("inspect", "--format", "{{.Id}}", self.container), container_id)
        self.assertEqual(self.docker("exec", self.container, "cat", "/home/user/sentinel"), "sentinel")
        inspect = json.loads(self.docker("inspect", self.container))[0]
        self.assertFalse(inspect["HostConfig"]["Privileged"])
        self.assertFalse(inspect["HostConfig"]["Binds"])
        self.assertNotIn("synthetic-first-token", json.dumps(inspect["Config"]))
        self.assertNotIn("synthetic-second-token", json.dumps(inspect["Config"]))
        self.assertEqual(self.docker("logs", self.container), "")

        self.assertEqual(self.dispatch("delete", token=None).returncode, 0)
        self.assertEqual(self.dispatch("delete", token=None).returncode, 0)
        self.assertEqual(self.dispatch("pause", token=None).returncode, 0)
        self.assertEqual(self.docker("ps", "--all", "--quiet", "--filter", "name=^/" + self.container + "$"), "")

    def test_repeat_spawn_replaces_only_bootstrap_and_keeps_the_running_orb(self):
        self.assertEqual(self.dispatch("spawn").returncode, 0)
        old_pid = self.record("synthetic-first-token")[3]
        container_id = self.docker("inspect", "--format", "{{.Id}}", self.container)
        self.docker("exec", self.container, "bash", "-c", "sleep infinity & echo $! > /tmp/unrelated-pid")

        result = self.dispatch("spawn", token="synthetic-refreshed-token", amp_url=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record("synthetic-refreshed-token")[:3], ["user", "synthetic-refreshed-token", ""])
        self.assertEqual(self.docker("inspect", "--format", "{{.Id}}", self.container), container_id)
        self.docker("exec", self.container, "bash", "-c", 'test ! -e "/proc/$1"', "_", old_pid)
        self.docker("exec", self.container, "bash", "-c", 'kill -0 "$(cat /tmp/unrelated-pid)"')

    def test_unowned_name_collisions_are_rejected_for_every_operation(self):
        for labels in ([], ["--label", "dev.palka.managed=true", "--label", "dev.palka.orb-id=other"]):
            with self.subTest(labels=labels):
                unowned_id = self.docker("run", "--detach", "--name", self.container, *labels, self.image, "sleep", "infinity")
                self.docker("exec", self.container, "bash", "-c", "printf untouched > /home/user/unowned")
                for operation in ("spawn", "pause", "delete"):
                    result = self.dispatch(operation)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("unowned resource", result.stderr)
                    self.assertEqual(self.docker("inspect", "--format", "{{.Id}}", self.container), unowned_id)
                    self.assertEqual(self.docker("inspect", "--format", "{{.State.Running}}", self.container), "true")
                    self.assertEqual(self.docker("exec", self.container, "cat", "/home/user/unowned"), "untouched")
                self.docker("rm", "--force", self.container)

    def test_unrelated_container_outside_the_namespace_is_untouched(self):
        unrelated_id = self.docker("run", "--detach", "--name", self.orb_id, self.image, "sleep", "infinity")
        self.addCleanup(self.docker, "rm", "--force", unrelated_id)
        for operation in ("spawn", "pause", "spawn", "delete"):
            result = self.dispatch(operation)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.docker("inspect", "--format", "{{.State.Running}}", unrelated_id), "true")

    def test_invalid_jobs_and_failed_image_creation_do_not_disclose_credentials(self):
        for operation, job in (
            ("synthetic-first-token", {}),
            ("spawn", {"ORB_ID": "../synthetic-first-token"}),
            ("spawn", {"ORB_ID": "--synthetic-first-token"}),
            ("spawn", {"ORB_ID": "synthetic-first-token; touch /tmp/not-run"}),
            ("spawn", {"ORB_TOKEN": ""}),
            ("spawn", {"ORB_IMAGE": ""}),
            ("spawn", {"ORB_IMAGE": "--privileged"}),
            ("spawn", {"ORB_IMAGE": "invalid image synthetic-first-token"}),
        ):
            with self.subTest(operation=operation, job=job):
                result = self.dispatch(operation, **job)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.docker("ps", "--all", "--quiet", "--filter", "name=^/" + self.container + "$"), "")

    def test_unreachable_daemon_is_not_reported_as_an_absent_orb(self):
        for operation in ("spawn", "pause", "delete"):
            with self.subTest(operation=operation):
                result = self.dispatch(operation, DOCKER_HOST="unix:///tmp/palka-missing-" + uuid.uuid4().hex)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Docker", result.stderr)

    def test_failed_bootstrap_is_reported_without_disclosure_and_can_be_retried(self):
        result = self.dispatch("spawn", amp_url="https://bootstrap-failure.invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker exec failed", result.stderr)
        result = self.dispatch("spawn", token="synthetic-retry-token")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record("synthetic-retry-token")[:3], ["user", "synthetic-retry-token", "https://amp.invalid"])


if __name__ == "__main__":
    unittest.main()

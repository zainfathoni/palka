"""Exercise the dispatch contract against disposable real Docker containers."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor


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
        state = tempfile.TemporaryDirectory(prefix="palka-test-state-")
        self.addCleanup(state.cleanup)
        self.state = state.name
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

    def job_env(self, operation, token="synthetic-first-token", amp_url="https://amp.invalid", **job):
        env = {key: value for key, value in os.environ.items() if not key.startswith("ORB_") and key != "AMP_URL"}
        env.update(ORB_OPERATION=operation, ORB_ID=self.orb_id, ORB_IMAGE=self.image)
        env["PALKA_STATE_DIR"] = self.state
        env["AMP_ORB_PROVIDER_TOKEN"] = "synthetic-provider-token"
        if token is not None:
            env["ORB_TOKEN"] = token
        if amp_url is not None:
            env["AMP_URL"] = amp_url
        env.update(job)
        return env

    def dispatch(self, operation, token="synthetic-first-token", amp_url="https://amp.invalid", **job):
        result = subprocess.run(
            [str(ROOT / "dispatch.py")],
            env=self.job_env(operation, token, amp_url, **job),
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

    def test_concurrent_spawn_jobs_use_one_container_and_one_bootstrap(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.dispatch("spawn"), range(4)))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.record("synthetic-first-token")
        processes = self.docker("top", self.container, "-eo", "pid,args").splitlines()
        self.assertEqual(sum(line.split(maxsplit=1)[-1] == "sleep infinity" for line in processes), 2)

    def fault_env(self, stage, **job):
        bindir = Path(self.state) / "bin"
        bindir.mkdir(exist_ok=True)
        wrapper = bindir / "docker"
        if not wrapper.exists():
            wrapper.symlink_to(ROOT / "tests/fixtures/fault-docker")
        return self.job_env(
            "spawn", PATH=str(bindir) + os.pathsep + os.environ["PATH"],
            PALKA_TEST_DOCKER=shutil.which("docker"), PALKA_TEST_STAGE=stage,
            PALKA_TEST_BARRIER=self.state, **job,
        )

    def wait_for_barrier(self, orb_id):
        for _ in range(300):
            if (Path(self.state) / orb_id).exists():
                return
            time.sleep(0.02)
        self.fail("Dispatch did not reach the Docker fault barrier.")

    @contextmanager
    def running_dispatch(self, env):
        process = subprocess.Popen(
            [str(ROOT / "dispatch.py")], env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        try:
            yield process
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate(timeout=8)

    def test_deadline_stops_runtime_work_and_allows_a_retry(self):
        start = time.monotonic()
        env = self.fault_env("create", PALKA_JOB_TIMEOUT_SECONDS="2")
        with self.running_dispatch(env) as process:
            stdout, stderr = process.communicate(timeout=8)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("timed out", stderr)
        self.assertLess(time.monotonic() - start, 5)
        self.assertNotIn("synthetic-first-token", stdout + stderr)
        created = self.docker("inspect", "--format", "{{.Id}}", self.container)
        result = self.dispatch("spawn")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.docker("inspect", "--format", "{{.Id}}", self.container), created)

    def test_lock_deadline_does_not_block_an_independent_orb(self):
        other = self.orb_id + "-other"
        self.addCleanup(self.docker, "rm", "--force", "palka-" + other)
        with self.running_dispatch(self.fault_env("start")) as first:
            self.wait_for_barrier(self.orb_id)
            result = self.dispatch("pause", PALKA_JOB_TIMEOUT_SECONDS="0.4")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("timed out waiting", result.stderr)
            with self.running_dispatch(self.fault_env("start", ORB_ID=other)) as second:
                self.wait_for_barrier(other)
                (Path(self.state) / "release").touch()
                for process in (first, second):
                    stdout, stderr = process.communicate(timeout=8)
                    self.assertEqual(process.returncode, 0, stderr)
                    self.assertNotIn("synthetic-first-token", stdout + stderr)

    def test_interrupted_jobs_recover_from_docker_state(self):
        for stage, operation in (("create", "spawn"), ("start", "spawn"), ("exec", "spawn"), ("stop", "pause"), ("rm", "delete")):
            with self.subTest(stage=stage):
                self.assertEqual(self.dispatch("delete").returncode, 0)
                if operation != "spawn":
                    self.assertEqual(self.dispatch("spawn").returncode, 0)
                    self.docker("exec", self.container, "bash", "-c", "printf sentinel > /home/user/sentinel")
                (Path(self.state) / self.orb_id).unlink(missing_ok=True)
                env = self.fault_env(stage, ORB_OPERATION=operation)
                with self.running_dispatch(env) as process:
                    self.wait_for_barrier(self.orb_id)
                    if operation != "delete":
                        saved_id = self.docker("inspect", "--format", "{{.Id}}", self.container)
                    process.terminate()
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertNotEqual(process.returncode, 0)
                    self.assertIn("interrupted", stderr)
                    self.assertNotIn("synthetic-first-token", stdout + stderr)
                result = self.dispatch("spawn", token="synthetic-retry-token")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.record("synthetic-retry-token")
                if operation != "delete":
                    self.assertEqual(self.docker("inspect", "--format", "{{.Id}}", self.container), saved_id)
                if operation == "pause":
                    self.assertEqual(self.docker("exec", self.container, "cat", "/home/user/sentinel"), "sentinel")
                processes = self.docker("top", self.container, "-eo", "pid,args").splitlines()
                self.assertEqual(sum(line.split(maxsplit=1)[-1] == "sleep infinity" for line in processes), 2)

    def test_spawn_pause_and_spawn_delete_have_serial_final_states(self):
        for other in ("pause", "delete"):
            with self.subTest(other=other):
                self.assertEqual(self.dispatch("spawn").returncode, 0)
                self.docker("exec", self.container, "bash", "-c", "printf sentinel > /home/user/sentinel")
                saved_id = self.docker("inspect", "--format", "{{.Id}}", self.container)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(self.dispatch, ("spawn", other)))
                for result in results:
                    self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.dispatch("spawn").returncode, 0)
                if other == "pause":
                    self.assertEqual(self.docker("inspect", "--format", "{{.Id}}", self.container), saved_id)
                    self.assertEqual(self.docker("exec", self.container, "cat", "/home/user/sentinel"), "sentinel")
                else:
                    self.assertNotEqual(self.docker("inspect", "--format", "{{.Id}}", self.container), saved_id)
                    self.docker("exec", self.container, "test", "!", "-e", "/home/user/sentinel")

    def test_resume_waits_for_an_interrupted_stop_that_is_still_in_progress(self):
        self.assertEqual(self.dispatch("spawn").returncode, 0)
        self.docker("exec", self.container, "bash", "-c", "printf sentinel > /home/user/sentinel")
        self.docker("exec", self.container, "bash", "-c", 'read -r child rest < /proc/1/task/1/children; kill -STOP "$child"')
        with self.running_dispatch(self.job_env("pause")) as process:
            events = self.docker(
                "events", "--since", "0", "--until", str(int(time.time()) + 2),
                "--filter", "container=" + self.container, "--filter", "event=kill",
                "--format", '{{index .Actor.Attributes "signal"}}',
            )
            self.assertIn("15", events.splitlines())
            process.terminate()
            process.communicate(timeout=5)
        result = self.dispatch("spawn")
        self.assertEqual(result.returncode, 0, result.stderr)
        time.sleep(11)
        self.assertEqual(self.docker("inspect", "--format", "{{.State.Running}}", self.container), "true")
        self.assertEqual(self.docker("exec", self.container, "cat", "/home/user/sentinel"), "sentinel")


if __name__ == "__main__":
    unittest.main()

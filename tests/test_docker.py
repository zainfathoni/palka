"""Exercise the dispatch contract against disposable real Docker containers."""

from contextlib import contextmanager
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor


ROOT = Path(__file__).resolve().parent.parent


class DockerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage_path = json.loads(subprocess.check_output(
            ["docker", "info", "--format", "{{json .DockerRootDir}}"], text=True, timeout=30,
        ))
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
        self.snapshot_ids = []
        self.addCleanup(self.remove_test_snapshots)
        self.orb_id = "test.orb_" + uuid.uuid4().hex
        self.container = "palka-" + self.orb_id
        self.addCleanup(self.remove_test_orb, self.orb_id)

    def remove_test_orb(self, orb_id):
        subprocess.run(
            ["docker", "rm", "--force", "--volumes", "palka-" + orb_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )

    def docker(self, *args):
        return subprocess.check_output(["docker", *args], text=True, timeout=30).strip()

    def snapshot_ref(self, snapshot_id):
        self.snapshot_ids.append(snapshot_id)
        return "palka-snapshot:" + hashlib.sha256(snapshot_id.encode()).hexdigest()

    def remove_test_snapshots(self):
        for snapshot_id in set(self.snapshot_ids):
            digest = hashlib.sha256(snapshot_id.encode()).hexdigest()
            images = self.docker("image", "ls", "--all", "--quiet", "--filter", "label=dev.palka.snapshot-id=" + digest)
            if images:
                self.docker("image", "rm", "--force", *set(images.splitlines()))

    def job_env(self, operation, token="synthetic-first-token", amp_url="https://amp.invalid", **job):
        env = {key: value for key, value in os.environ.items() if not key.startswith(("ORB_", "PALKA_")) and key != "AMP_URL"}
        env.update(ORB_OPERATION=operation, ORB_ID=self.orb_id, ORB_IMAGE=self.image)
        env.update(
            PALKA_STATE_DIR=self.state, PALKA_CPUS="0.5", PALKA_MEMORY_BYTES=str(128 * 1024**2),
            PALKA_PIDS_LIMIT="64", PALKA_MAX_RUNNING="8", PALKA_MIN_FREE_BYTES=str(1024**2),
            PALKA_MAX_USED_BYTES=str(2**60), PALKA_STORAGE_PATH=self.storage_path,
        )
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

    def test_diagnostics_report_sanitized_results_without_mutation(self):
        self.assertEqual(self.dispatch("spawn").returncode, 0)
        before = {path.name: path.read_bytes() for path in Path(self.state).iterdir()}

        def diagnose(**override):
            env = self.job_env("spawn")
            env.update(override)
            result = subprocess.run(
                ["python3", str(ROOT / "scripts/diagnose.py")], env=env,
                capture_output=True, text=True, timeout=30,
            )
            for secret in (self.orb_id, self.state, self.storage_path, "synthetic-first-token", "synthetic-provider-token", "amp.invalid"):
                self.assertNotIn(secret, result.stdout + result.stderr)
            return result

        result = diagnose()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["runtime_reachable"])
        self.assertEqual(report["limits"]["memory_bytes"], 128 * 1024**2)
        self.assertEqual(report["last_operations"]["spawn"]["count"], 1)
        self.assertEqual(report["last_operations"]["spawn"]["failed"], 0)
        self.assertGreater(report["last_operations"]["spawn"]["max_seconds"], 0)
        self.assertEqual(before, {path.name: path.read_bytes() for path in Path(self.state).iterdir()})
        self.assertEqual(self.docker("inspect", "--format", "{{.State.Running}}", self.container), "true")

        failed = self.dispatch("spawn", PALKA_CPUS="0.75")
        self.assertNotEqual(failed.returncode, 0)
        report = json.loads(diagnose().stdout)
        self.assertEqual(report["last_operations"]["spawn"]["failed"], 1)
        bad = diagnose(DOCKER_HOST="unix:///nonexistent/synthetic-provider-token.sock")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("Docker version failed", json.loads(bad.stdout)["error"])
        (Path(self.state) / ("orb-" + self.orb_id + ".result")).write_text(
            '{"operation":"synthetic-provider-token","complete":true,"seconds":1}'
        )
        bad = diagnose()
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("invalid", json.loads(bad.stdout)["error"])

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
        for stage, operation in (("create", "spawn"), ("start", "spawn"), ("exec", "spawn"), ("rm", "delete")):
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

    def test_failed_start_does_not_prevent_pause_or_delete(self):
        for pause_first in (False, True):
            with self.subTest(pause_first=pause_first):
                self.assertEqual(self.dispatch("spawn").returncode, 0)
                self.docker("exec", self.container, "bash", "-c", "printf retained > /home/user/sentinel")
                saved_id = self.docker("inspect", "--format", "{{.Id}}", self.container)
                # Break only this disposable container's configured user, not its image.
                self.docker("exec", "--user", "0", self.container, "sed", "-i", "/^user:/d", "/etc/passwd")
                self.assertEqual(self.dispatch("pause").returncode, 0)
                failed = self.dispatch("spawn")
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn("Docker start failed", failed.stderr)
                if pause_first:
                    paused = self.dispatch("pause")
                    self.assertEqual(paused.returncode, 0, paused.stderr)
                    self.assertEqual(self.docker("inspect", "--format", "{{.State.Running}}", self.container), "false")
                    copied = Path(self.state) / "sentinel"
                    self.docker("cp", self.container + ":/home/user/sentinel", str(copied))
                    self.assertEqual(copied.read_text(), "retained")
                    self.assertNotEqual(self.dispatch("spawn").returncode, 0)
                deleted = self.dispatch("delete")
                self.assertEqual(deleted.returncode, 0, deleted.stderr)
                self.assertEqual(self.docker("ps", "--all", "--quiet", "--filter", "id=" + saved_id), "")

    def test_resume_refuses_an_unknown_stop_and_preserves_the_disk(self):
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
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uncertain", result.stderr)
        time.sleep(11)
        self.assertNotEqual(self.dispatch("spawn").returncode, 0)
        self.assertEqual(self.docker("inspect", "--format", "{{.State.Running}}", self.container), "false")
        copied = Path(self.state) / "sentinel"
        self.docker("cp", self.container + ":/home/user/sentinel", str(copied))
        self.assertEqual(copied.read_text(), "sentinel")

    def test_unknown_start_refuses_pause_but_allows_scoped_delete(self):
        result = subprocess.run(
            [str(ROOT / "dispatch.py")], env=self.fault_env("start", PALKA_TEST_FAIL="1"),
            capture_output=True, text=True, timeout=20,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("synthetic-first-token", result.stdout + result.stderr)
        saved_id = self.docker("inspect", "--format", "{{.Id}}", self.container)
        for operation in ("spawn", "pause"):
            refused = self.dispatch(operation)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("uncertain", refused.stderr)
        failed_delete = subprocess.run(
            [str(ROOT / "dispatch.py")],
            env=self.fault_env("rm", PALKA_TEST_FAIL="1", ORB_OPERATION="delete"),
            capture_output=True, text=True, timeout=20,
        )
        self.assertNotEqual(failed_delete.returncode, 0)
        self.assertNotIn("synthetic-first-token", failed_delete.stdout + failed_delete.stderr)
        self.assertIn("uncertain", self.dispatch("pause").stderr)
        deleted = self.dispatch("delete")
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        self.assertEqual(self.docker("ps", "--all", "--quiet", "--filter", "id=" + saved_id), "")

    def test_snapshot_fan_out_keeps_independent_disks_and_no_spawn_credentials(self):
        snapshot_id = "test-snapshot-" + uuid.uuid4().hex
        reference = self.snapshot_ref(snapshot_id)
        self.assertEqual(self.dispatch("spawn").returncode, 0)
        self.record("synthetic-first-token")
        original = self.docker("inspect", "--format", "{{.Id}}", self.container)
        self.docker("exec", self.container, "bash", "-c", "printf prepared > /home/user/sentinel")
        result = self.dispatch("pause", ORB_SNAPSHOT_ID=snapshot_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.docker("image", "inspect", reference)
        self.assertEqual(self.dispatch("spawn", ORB_SNAPSHOT_ID=snapshot_id).returncode, 0)
        self.assertEqual(self.docker("inspect", "--format", "{{.Id}}", self.container), original)
        self.docker("exec", self.container, "bash", "-c", "printf original > /home/user/sentinel")

        copies = [self.orb_id + "-a", self.orb_id + "-b"]
        for orb_id in copies:
            self.addCleanup(self.docker, "rm", "--force", "--volumes", "palka-" + orb_id)
            result = self.dispatch("spawn", token="synthetic-copy-token", amp_url=None, ORB_ID=orb_id, ORB_SNAPSHOT_ID=snapshot_id)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.docker("exec", "palka-" + orb_id, "cat", "/home/user/sentinel"), "prepared")
            record = self.docker("exec", "palka-" + orb_id, "cat", "/tmp/bootstrap-record").splitlines()
            self.assertEqual(record[:3], ["user", "synthetic-copy-token", ""])
        self.docker("exec", "palka-" + copies[0], "bash", "-c", "printf changed > /home/user/sentinel")
        self.assertEqual(self.dispatch("delete", ORB_ID=copies[0]).returncode, 0)
        self.assertEqual(self.docker("exec", "palka-" + copies[1], "cat", "/home/user/sentinel"), "prepared")
        self.assertEqual(self.docker("exec", self.container, "cat", "/home/user/sentinel"), "original")
        self.assertEqual(self.dispatch("spawn", ORB_ID=copies[0], ORB_SNAPSHOT_ID=snapshot_id).returncode, 0)
        self.assertEqual(self.docker("exec", "palka-" + copies[0], "cat", "/home/user/sentinel"), "prepared")
        archive = subprocess.check_output(["docker", "image", "save", reference], timeout=60)
        with tarfile.open(fileobj=io.BytesIO(archive)) as saved:
            for entry in saved:
                if not entry.isfile():
                    continue
                payload = saved.extractfile(entry).read()
                if payload.startswith(b"\x1f\x8b"):
                    payload = gzip.decompress(payload)
                self.assertFalse(payload.startswith(b"\x28\xb5\x2f\xfd"), "Snapshot test needs a zstd layer decoder.")
                for marker in (b"synthetic-first-token", b"synthetic-copy-token", b"synthetic-provider-token"):
                    self.assertFalse(marker in payload, "Snapshot contains a synthetic credential marker.")

    def test_interrupted_snapshot_keeps_the_prior_copy_and_serializes_readers(self):
        snapshot_id = "test-snapshot-" + uuid.uuid4().hex
        reference = self.snapshot_ref(snapshot_id)
        self.assertEqual(self.dispatch("spawn").returncode, 0)
        self.docker("exec", self.container, "bash", "-c", "printf old > /home/user/sentinel")
        self.assertEqual(self.dispatch("pause", ORB_SNAPSHOT_ID=snapshot_id).returncode, 0)
        old_image = self.docker("image", "inspect", "--format", "{{.Id}}", reference)
        self.assertEqual(self.dispatch("spawn").returncode, 0)
        self.docker("exec", self.container, "bash", "-c", "printf new > /home/user/sentinel")
        reader = self.orb_id + "-reader"
        self.addCleanup(self.docker, "rm", "--force", "palka-" + reader)
        env = self.fault_env("commit", ORB_OPERATION="pause", ORB_SNAPSHOT_ID=snapshot_id)
        with self.running_dispatch(env) as process:
            self.wait_for_barrier(self.orb_id)
            self.assertEqual(self.docker("image", "inspect", "--format", "{{.Id}}", reference), old_image)
            result = self.dispatch("spawn", ORB_ID=reader, ORB_SNAPSHOT_ID=snapshot_id, PALKA_JOB_TIMEOUT_SECONDS="0.4")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("timed out waiting", result.stderr)
            process.terminate()
            process.communicate(timeout=5)
        self.assertEqual(self.dispatch("spawn", ORB_ID=reader, ORB_SNAPSHOT_ID=snapshot_id).returncode, 0)
        self.assertEqual(self.docker("exec", "palka-" + reader, "cat", "/home/user/sentinel"), "old")
        self.assertEqual(self.dispatch("pause", ORB_SNAPSHOT_ID=snapshot_id).returncode, 0)
        self.assertNotEqual(self.docker("image", "inspect", "--format", "{{.Id}}", reference), old_image)
        self.assertEqual(self.docker("exec", "palka-" + reader, "cat", "/home/user/sentinel"), "old")

    def test_snapshot_writers_publish_one_complete_disk(self):
        snapshot_id = "test-snapshot-" + uuid.uuid4().hex
        self.snapshot_ref(snapshot_id)
        other = self.orb_id + "-writer"
        reader = self.orb_id + "-reader"
        for orb_id in (other, reader):
            self.addCleanup(self.docker, "rm", "--force", "palka-" + orb_id)
        for orb_id, value in ((self.orb_id, "first-writer"), (other, "second-writer")):
            self.assertEqual(self.dispatch("spawn", ORB_ID=orb_id).returncode, 0)
            self.docker("exec", "palka-" + orb_id, "bash", "-c", 'printf %s "$1" > /home/user/sentinel', "_", value)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda orb_id: self.dispatch("pause", ORB_ID=orb_id, ORB_SNAPSHOT_ID=snapshot_id), (self.orb_id, other)))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.dispatch("spawn", ORB_ID=reader, ORB_SNAPSHOT_ID=snapshot_id).returncode, 0)
        self.assertIn(self.docker("exec", "palka-" + reader, "cat", "/home/user/sentinel"), {"first-writer", "second-writer"})

    def test_snapshot_errors_do_not_fall_back_to_the_base_image(self):
        snapshot_id = "test-snapshot-" + uuid.uuid4().hex
        reference = self.snapshot_ref(snapshot_id)
        self.docker("image", "tag", self.image, reference)
        self.addCleanup(self.docker, "image", "rm", reference)
        result = self.dispatch("spawn", ORB_SNAPSHOT_ID=snapshot_id)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unowned resource", result.stderr)
        env = self.fault_env("image inspect", ORB_SNAPSHOT_ID=snapshot_id, PALKA_TEST_FAIL="1")
        with self.running_dispatch(env) as process:
            stdout, stderr = process.communicate(timeout=5)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("Docker image failed", stderr)
        self.assertNotIn("synthetic-first-token", stdout + stderr)
        self.assertEqual(self.docker("ps", "--all", "--quiet", "--filter", "name=^/" + self.container + "$"), "")

    def test_mounted_volume_data_is_rejected_instead_of_omitted_from_a_snapshot(self):
        snapshot_id = "test-snapshot-" + uuid.uuid4().hex
        self.snapshot_ref(snapshot_id)
        self.docker(
            "run", "--detach", "--name", self.container, "--volume", "/home/user",
            "--label", "dev.palka.managed=true", "--label", "dev.palka.orb-id=" + self.orb_id,
            self.image, "sleep", "infinity",
        )
        self.docker("exec", self.container, "bash", "-c", "printf sentinel > /home/user/sentinel")
        result = self.dispatch("pause", ORB_SNAPSHOT_ID=snapshot_id)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mounted disk content", result.stderr)
        self.assertEqual(self.docker("inspect", "--format", "{{.State.Running}}", self.container), "true")
        self.assertEqual(self.docker("exec", self.container, "cat", "/home/user/sentinel"), "sentinel")

    def test_created_orb_has_operator_selected_limits(self):
        result = self.dispatch("spawn")
        self.assertEqual(result.returncode, 0, result.stderr)
        host = json.loads(self.docker("inspect", self.container))[0]["HostConfig"]
        self.assertEqual(host["NanoCpus"], 500_000_000)
        self.assertEqual(host["Memory"], 134_217_728)
        self.assertEqual(host["MemorySwap"], 134_217_728)
        self.assertEqual(host["PidsLimit"], 64)
        self.assertEqual(host["LogConfig"], {"Type": "local", "Config": {"max-size": "5m", "max-file": "2"}})
        self.assertEqual(self.docker("exec", self.container, "cat", "/sys/fs/cgroup/memory.max"), "134217728")
        self.assertEqual(self.docker("exec", self.container, "cat", "/sys/fs/cgroup/pids.max"), "64")

    def test_concurrent_admission_allows_only_one_running_orb(self):
        other = self.orb_id + "-other"
        self.addCleanup(self.remove_test_orb, other)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda orb: self.dispatch("spawn", ORB_ID=orb, PALKA_MAX_RUNNING="1"), (self.orb_id, other)))
        self.assertEqual(sum(result.returncode == 0 for result in results), 1)
        refused = next(result for result in results if result.returncode != 0)
        self.assertIn("running-Orb limit", refused.stderr)

    def test_storage_pressure_refuses_resume_without_losing_the_retained_disk(self):
        self.assertEqual(self.dispatch("spawn").returncode, 0)
        self.docker("exec", self.container, "bash", "-c", "printf sentinel > /home/user/sentinel")
        saved_id = self.docker("inspect", "--format", "{{.Id}}", self.container)
        self.assertEqual(self.dispatch("pause").returncode, 0)
        for config in ({"PALKA_MIN_FREE_BYTES": str(2**60)}, {"PALKA_MAX_USED_BYTES": "1"}):
            with self.subTest(config=config):
                result = self.dispatch("spawn", **config)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("storage", result.stderr)
                self.assertEqual(self.docker("inspect", "--format", "{{.State.Running}}", self.container), "false")
                self.assertEqual(self.docker("inspect", "--format", "{{.Id}}", self.container), saved_id)
        self.assertEqual(self.dispatch("spawn").returncode, 0)
        self.assertEqual(self.docker("exec", self.container, "cat", "/home/user/sentinel"), "sentinel")

    def test_invalid_limits_and_changed_resume_limits_do_not_change_disk_state(self):
        for config in (
            {"PALKA_CPUS": "synthetic-first-token"}, {"PALKA_CPUS": "nan"},
            {"PALKA_MEMORY_BYTES": "6291455"}, {"PALKA_PIDS_LIMIT": "0"},
            {"PALKA_MAX_RUNNING": "-1"}, {"PALKA_MIN_FREE_BYTES": "0"},
            {"PALKA_MAX_USED_BYTES": ""}, {"PALKA_STORAGE_PATH": "relative"},
            {"PALKA_STATE_DIR": "relative"}, {"PALKA_JOB_TIMEOUT_SECONDS": "nan"},
        ):
            with self.subTest(config=config):
                result = self.dispatch("spawn", **config)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.docker("ps", "--all", "--quiet", "--filter", "name=^/" + self.container + "$"), "")
        self.assertEqual(self.dispatch("spawn").returncode, 0)
        self.docker("exec", self.container, "bash", "-c", "printf sentinel > /home/user/sentinel")
        self.assertEqual(self.dispatch("pause").returncode, 0)
        for config in ({"PALKA_CPUS": "0.25"}, {"PALKA_MEMORY_BYTES": "268435456"}, {"PALKA_PIDS_LIMIT": "32"}):
            result = self.dispatch("spawn", **config)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("limits differ", result.stderr)
            self.assertEqual(self.docker("inspect", "--format", "{{.State.Running}}", self.container), "false")
        self.assertEqual(self.dispatch("spawn").returncode, 0)
        self.assertEqual(self.docker("exec", self.container, "cat", "/home/user/sentinel"), "sentinel")

    def test_capacity_denial_keeps_a_stopped_disk_and_pause_releases_the_slot(self):
        other = self.orb_id + "-other"
        self.addCleanup(self.remove_test_orb, other)
        self.assertEqual(self.dispatch("spawn", PALKA_MAX_RUNNING="1").returncode, 0)
        self.docker("exec", self.container, "bash", "-c", "printf sentinel > /home/user/sentinel")
        saved_id = self.docker("inspect", "--format", "{{.Id}}", self.container)
        self.assertEqual(self.dispatch("pause").returncode, 0)
        self.assertEqual(self.dispatch("spawn", ORB_ID=other, PALKA_MAX_RUNNING="1").returncode, 0)
        result = self.dispatch("spawn", PALKA_MAX_RUNNING="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("running-Orb limit", result.stderr)
        self.assertEqual(self.docker("inspect", "--format", "{{.State.Running}}", self.container), "false")
        self.assertEqual(self.dispatch("delete", ORB_ID=other).returncode, 0)
        self.assertEqual(self.dispatch("spawn", PALKA_MAX_RUNNING="1").returncode, 0)
        self.assertEqual(self.docker("inspect", "--format", "{{.Id}}", self.container), saved_id)
        self.assertEqual(self.docker("exec", self.container, "cat", "/home/user/sentinel"), "sentinel")

    def test_pending_spawn_reserves_capacity_until_recovery(self):
        other = self.orb_id + "-other"
        self.addCleanup(self.remove_test_orb, other)
        with self.running_dispatch(self.fault_env("create", PALKA_MAX_RUNNING="1")) as process:
            self.wait_for_barrier(self.orb_id)
            result = self.dispatch("spawn", ORB_ID=other, PALKA_MAX_RUNNING="1")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("running-Orb limit", result.stderr)
            process.terminate()
            process.communicate(timeout=5)
        self.assertEqual(self.dispatch("spawn", PALKA_MAX_RUNNING="1").returncode, 0)
        self.record("synthetic-first-token")

    def test_resource_pressure_is_contained_and_the_disk_survives(self):
        result = self.dispatch("spawn")
        self.assertEqual(result.returncode, 0, result.stderr)
        # Check kernel limits before a pressure workload can allocate resources.
        self.assertEqual(self.docker("exec", self.container, "cat", "/sys/fs/cgroup/memory.max"), "134217728")
        self.assertEqual(self.docker("exec", self.container, "cat", "/sys/fs/cgroup/pids.max"), "64")
        quota, period = map(int, self.docker("exec", self.container, "cat", "/sys/fs/cgroup/cpu.max").split())
        self.assertEqual(quota * 2, period)
        self.docker("exec", self.container, "bash", "-c", "printf sentinel > /home/user/sentinel")
        memory = subprocess.run(
            ["docker", "exec", self.container, "bash", "-c", 'text=x; for ((i=0; i<30; i++)); do text=$text$text; done'],
            capture_output=True, timeout=20,
        )
        self.assertNotEqual(memory.returncode, 0)
        events = dict(line.split() for line in self.docker("exec", self.container, "cat", "/sys/fs/cgroup/memory.events").splitlines())
        self.assertGreater(int(events["oom_kill"]), 0)
        subprocess.run(
            ["docker", "exec", self.container, "bash", "-c", 'for i in {1..90}; do sleep 2 & done; wait'],
            capture_output=True, timeout=20,
        )
        # Fork refusal can end the shell before its two-second children exit.
        time.sleep(3)
        events = dict(line.split() for line in self.docker("exec", self.container, "cat", "/sys/fs/cgroup/pids.events").splitlines())
        self.assertGreater(int(events["max"]), 0)
        before = dict(line.split() for line in self.docker("exec", self.container, "cat", "/sys/fs/cgroup/cpu.stat").splitlines())
        self.docker("exec", self.container, "bash", "-c", 'timeout 2s yes >/dev/null || test "$?" -eq 124')
        after = dict(line.split() for line in self.docker("exec", self.container, "cat", "/sys/fs/cgroup/cpu.stat").splitlines())
        self.assertGreater(int(after["nr_throttled"]), int(before["nr_throttled"]))
        self.assertEqual(self.dispatch("pause").returncode, 0)
        self.assertEqual(self.dispatch("spawn").returncode, 0)
        self.assertEqual(self.docker("exec", self.container, "cat", "/home/user/sentinel"), "sentinel")


if __name__ == "__main__":
    unittest.main()

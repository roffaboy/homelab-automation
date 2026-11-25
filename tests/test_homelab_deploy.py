"""Tests of the automatic deploys (roles/autodeploy/files/homelab_deploy.py).

Git is real (temporary repositories: a "GitHub" that is a folder, the server's copy, your config repo);
what needs the server (docker, restic, sudo, Ansible, Telegram) is a fake that records what was asked.

Run: python3 -m unittest discover -s tests
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "roles", "autodeploy", "files"))
import homelab_common  # noqa: E402
import homelab_deploy  # noqa: E402

os.environ.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
RESTIC = ("sudo", "--preserve-env=RESTIC_REPOSITORY,RESTIC_PASSWORD", "restic")
PLAYBOOK = (".venv/bin/ansible-playbook",)
ANSIBLE_FAILURE = """\
TASK [app_config : Seerr | Telegram notifications] *****************************
fatal: [homelab-host]: FAILED! => {"changed": false,
    msg: 'POST http://127.0.0.1:5055/api/v1/settings/notifications/telegram failed:
      HTTP 500'
}
"""


def git(path, *args):
    return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, check=True).stdout.strip()


def commit(path, name, text="x", message=None):
    """A commit that adds one file."""
    os.makedirs(os.path.dirname(f"{path}/{name}"), exist_ok=True)
    with open(f"{path}/{name}", "w") as file:
        file.write(text)
    git(path, "add", name)
    git(path, "commit", "-q", "-m", message or f"change {name}")
    return git(path, "rev-parse", "HEAD")


def clone(origin, path):
    subprocess.run(["git", "clone", "-q", origin, path], check=True)


class Fake:
    """Stands in for every command but git; `answers` maps the first words of a command to (code, output),
    or to a list of those, one for each time it is called."""

    def __init__(self):
        self.answers, self.calls = {}, []

    def __call__(self, *command, env=None, cwd=None):
        if command[0] == "git":
            result = subprocess.run(command, capture_output=True, text=True, env={**os.environ, **(env or {})})
            return result.returncode, result.stdout + result.stderr
        self.calls.append(command)
        if command[:2] == ("sudo", "mktemp"):
            return 0, "/tmp/restore\n"
        answer = next((self.answers[command[:n]] for n in range(len(command), 0, -1) if command[:n] in self.answers), (0, ""))
        return answer.pop(0) if isinstance(answer, list) else answer   # a list answers one call at a time

    def ran(self, *words):
        return [c for c in self.calls if c[:len(words)] == words]


class Messages:
    def __init__(self):
        self.sent = []

    def send(self, text, **extra):
        self.sent.append(text)


class Case(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = t = folder.name
        self.github, self.config_github, self.repo = f"{t}/github", f"{t}/config-github", f"{t}/server-copy"
        self.config, self.state, self.stack = f"{self.repo}/config", f"{t}/state", f"{t}/media-stack"
        for path in (self.github, self.config_github, self.state, self.stack):
            os.makedirs(path)
        for path in (self.github, self.config_github):
            git(path, "init", "-q", "-b", "main")
        commit(self.github, "provision.yml", "v1", "first")
        commit(self.config_github, "settings.yml", "a: 1", "first config")
        clone(self.github, self.repo)
        clone(self.config_github, self.config)
        self.env = {"HOMELAB_REPO_DIR": self.repo, "HOMELAB_STATE_DIR": self.state, "HOMELAB_REPO_URL": "https://example.com/repo",
                    "HOMELAB_SSH_KEY": "/nonexistent", "HOMELAB_STACK_DIRS": f"{self.stack} {t}/not-installed",
                    "HOMELAB_DATA_DIR": f"{t}/data", "PATH": os.environ["PATH"], "HOME": t}
        self.fake, self.messages = Fake(), Messages()
        self.fake.answers[("pgrep",)] = (1, "")   # no playbook is running

    def go(self, run=None):
        deploy = homelab_deploy.Deploy(self.env, run=run or self.fake, telegram=self.messages)
        with contextlib.redirect_stdout(io.StringIO()):   # the deploy prints Ansible's output for the journal
            return deploy.main()

    def playbook_runs(self):
        return self.fake.ran(*PLAYBOOK)

    def said(self, text):
        return any(text in message for message in self.messages.sent)


class Helpers(unittest.TestCase):
    def test_the_failure_is_the_task_above_the_fatal_line_and_its_message(self):
        task, error = homelab_deploy.failure_of(ANSIBLE_FAILURE)
        self.assertEqual(task, "[app_config : Seerr | Telegram notifications]")
        self.assertIn("POST http://127.0.0.1:5055/api/v1/settings/notifications/telegram failed:", error)
        self.assertIn("HTTP 500", error)

    def test_without_a_fatal_line_the_error_is_the_end_of_the_log(self):
        task, error = homelab_deploy.failure_of("one\ntwo\nthree\nfour\n")
        self.assertEqual((task, error), ("", "two\nthree\nfour"))

    def test_the_lock_is_taken_by_one_at_a_time(self):
        with tempfile.TemporaryDirectory() as folder:
            path = f"{folder}/lock"
            first = homelab_deploy.open_lock(path)
            self.assertIsNotNone(first)
            self.assertIsNone(homelab_deploy.open_lock(path))
            first.close()
            second = homelab_deploy.open_lock(path)
            self.assertIsNotNone(second)
            second.close()


class Deploys(Case):
    def test_nothing_new_does_nothing(self):
        self.assertEqual(self.go(), 0)
        self.assertEqual(self.playbook_runs(), [])
        self.assertEqual(self.messages.sent, [])

    def test_a_new_commit_is_deployed_and_reported(self):
        new = commit(self.github, "provision.yml", "v2", "a fix <here>")
        self.assertEqual(self.go(), 0)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), new)
        self.assertEqual([c[0] for c in self.fake.calls if c[0].startswith(".venv")],
                         [".venv/bin/pip", ".venv/bin/ansible-galaxy", ".venv/bin/ansible-playbook"])
        self.assertEqual(self.fake.ran("restic"), [])        # no new images: no backup
        self.assertTrue(self.said("a fix &lt;here&gt;"))     # the title is escaped, with a link to the commit
        self.assertTrue(self.said(f"https://example.com/repo/commit/{new}"))
        self.assertTrue(self.said("Updated to"))

    def test_a_new_commit_in_your_config_repo_is_deployed(self):
        commit(self.config_github, "settings.yml", "a: 2", "more config")
        self.assertEqual(self.go(), 0)
        self.assertEqual(len(self.playbook_runs()), 1)
        self.assertTrue(self.said("• config"))
        self.assertTrue(self.said("more config"))
        with open(f"{self.config}/settings.yml") as file:
            self.assertEqual(file.read(), "a: 2")

    def test_a_config_commit_you_have_not_pushed_is_not_new(self):
        commit(self.config, "settings.yml", "a: 3", "local only")
        self.assertEqual(self.go(), 0)
        self.assertEqual(self.playbook_runs(), [])

    def test_config_changes_you_have_not_committed_stop_the_update(self):
        commit(self.config_github, "settings.yml", "a: 2", "more config")
        with open(f"{self.config}/settings.yml", "w") as file:   # your own edit of the same file
            file.write("a: edited by hand")
        self.assertEqual(self.go(), 1)
        self.assertEqual(self.playbook_runs(), [])
        self.assertTrue(self.said("Uncommitted changes"))

    def test_a_run_of_the_playbook_by_hand_goes_first(self):
        commit(self.github, "provision.yml", "v2")
        self.fake.answers[("pgrep",)] = (0, "123")
        self.assertEqual(self.go(), 0)
        self.assertEqual(self.playbook_runs(), [])

    def test_a_failed_commit_waits_for_the_next_one(self):
        shutil.rmtree(self.config)   # also without a config repo
        commit(self.github, "provision.yml", "v2")
        self.fake.answers[PLAYBOOK] = (2, ANSIBLE_FAILURE)
        self.assertEqual(self.go(), 1)
        runs = len(self.playbook_runs())
        self.assertEqual(self.go(), 0)                        # the same commits: not tried again
        self.assertEqual(len(self.playbook_runs()), runs)
        commit(self.github, "provision.yml", "v3", "the fix")
        self.fake.answers.pop(PLAYBOOK)
        self.assertEqual(self.go(), 0)
        self.assertTrue(self.said("Updated to"))


class Backups(Case):
    def image_update(self):
        return commit(self.github, "roles/media_stack/defaults/main.yml", "images: new", "update the images")

    def test_new_images_are_backed_up_first_with_the_apps_stopped(self):
        self.image_update()
        self.assertEqual(self.go(), 0)
        calls = self.fake.calls
        stop = next(i for i, c in enumerate(calls) if c[:2] == ("docker", "compose") and c[-1] == "stop")
        backup_at = next(i for i, c in enumerate(calls) if "backup" in c and "restic" in c)
        start = next(i for i, c in enumerate(calls) if c[:2] == ("docker", "compose") and c[-1] == "start")
        self.assertLess(stop, backup_at)       # the apps are stopped while the data is copied
        self.assertLess(backup_at, start)
        backup = self.fake.ran(*RESTIC, "backup")[0]
        self.assertIn("pre-deploy", backup)
        self.assertIn("*/prometheus/data", backup)             # what is left out of every backup
        self.assertIn(self.stack, backup)
        self.assertNotIn(f"{self.folder}/not-installed", backup)   # a stack that is not installed is skipped
        self.assertEqual([c[-1] for c in self.fake.ran("docker", "compose")], ["stop", "start"])

    def test_a_failed_backup_skips_the_update(self):
        self.image_update()
        old = git(self.repo, "rev-parse", "HEAD")
        self.fake.answers[(*RESTIC, "backup")] = (1, "")
        self.assertEqual(self.go(), 1)
        self.assertEqual(self.playbook_runs(), [])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), old)
        self.assertEqual([c[-1] for c in self.fake.ran("docker", "compose")], ["stop", "start"])   # the apps run again
        self.assertTrue(self.said("Update skipped"))
        self.assertIsNotNone(homelab_common.State(self.state, self.repo).failed())

    def test_a_failed_deploy_after_a_backup_restores_it_and_deploys_the_previous_commit(self):
        old = git(self.repo, "rev-parse", "HEAD")
        self.image_update()
        self.fake.answers[PLAYBOOK] = [(2, ANSIBLE_FAILURE), (0, "")]   # fails, then the previous commit deploys
        self.assertEqual(self.go(), 1)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), old)                     # back on the previous commit
        self.assertTrue(self.fake.ran(*RESTIC, "restore"))
        self.assertTrue(self.fake.ran("sudo", "rsync"))
        self.assertTrue(self.fake.ran("sudo", "rm", "-rf", "/tmp/restore"))
        self.assertEqual(len(self.playbook_runs()), 2)                                 # the failed one, then the rollback
        self.assertTrue(self.said("[app_config : Seerr | Telegram notifications]"))
        self.assertTrue(self.said("HTTP 500"))
        self.assertTrue(self.said("Rolled back"))
        failure = homelab_common.State(self.state, self.repo).last_failure()
        self.assertIn("Seerr | Telegram notifications", failure[2])

    def test_a_failed_deploy_without_new_images_has_no_backup_to_restore(self):
        commit(self.github, "provision.yml", "v2")
        self.fake.answers[PLAYBOOK] = (2, ANSIBLE_FAILURE)
        self.assertEqual(self.go(), 1)
        self.assertEqual(self.fake.ran(*RESTIC, "restore"), [])
        self.assertTrue(self.said("Rollback failed too"))   # the fake playbook fails again, also on the previous commit

    def test_a_failing_config_change_is_left_for_you_to_fix(self):
        commit(self.config_github, "settings.yml", "a: 2", "a bad setting")
        self.fake.answers[PLAYBOOK] = (2, ANSIBLE_FAILURE)
        main_commit = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(self.go(), 1)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), main_commit)
        self.assertEqual(len(self.playbook_runs()), 1)               # no rollback run: main did not change
        self.assertTrue(self.said("Fix your config repo"))


class Hold(Case):
    def test_on_hold_nothing_is_deployed(self):
        commit(self.github, "provision.yml", "v2")
        homelab_common.State(self.state, self.repo).hold("a run from a branch")
        self.assertEqual(self.go(), 0)
        self.assertEqual(self.playbook_runs(), [])

    def test_a_hold_from_an_earlier_day_reminds_once_a_day(self):
        state = homelab_common.State(self.state, self.repo)
        state.hold("a run from a branch")
        yesterday = time.time() - 86400
        os.utime(f"{self.state}/hold", (yesterday, yesterday))
        self.go()
        self.go()
        self.assertEqual(len([m for m in self.messages.sent if "still on hold" in m]), 1)

    def test_main_lifts_the_hold_and_deploys_even_when_nothing_changed(self):
        state = homelab_common.State(self.state, self.repo)
        state.hold("a run from a branch")
        state.ask_for_main()
        self.assertEqual(self.go(), 0)
        self.assertEqual(len(self.playbook_runs()), 1)
        self.assertTrue(self.said("back to main"))
        self.assertIsNone(state.held())


if __name__ == "__main__":
    unittest.main()

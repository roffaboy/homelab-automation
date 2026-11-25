"""Tests of the automatic deploys (roles/autodeploy/files/homelab_deploy.py) and what they share with
the bot (homelab_common.py).

Git is real (temporary repositories: a GitHub that is a folder, the server's copy, your config
repo); everything that needs the server (docker, restic, sudo, Ansible, Telegram) is replaced by a
fake that records what was asked and answers as the test says.

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

ANSIBLE_FAILURE = """\
TASK [Gathering Facts] *********************************************************
ok: [homelab-host]

TASK [app_config : Seerr | Telegram notifications] *****************************
fatal: [homelab-host]: FAILED! => {"changed": false,
    msg: 'POST http://127.0.0.1:5055/api/v1/settings/notifications/telegram failed:
      HTTP 500'
}

PLAY RECAP *********************************************************************
"""


def git(path, *args):
    return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, check=True).stdout.strip()


def commit(path, name, text="x", message=None):
    """A commit that adds one file."""
    os.makedirs(os.path.dirname(os.path.join(path, name)) or path, exist_ok=True)
    with open(os.path.join(path, name), "w") as file:
        file.write(text)
    git(path, "add", name)
    git(path, "commit", "-q", "-m", message or f"change {name}")
    return git(path, "rev-parse", "HEAD")


def new_repo(path):
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "t")


class Sent:
    def __init__(self):
        self.messages = []

    def send(self, text, **extra):
        self.messages.append(text)


class Fake:
    """Stands in for every command but git. `answers` maps the first words of a command to (code, output)."""

    def __init__(self):
        self.answers = {}
        self.calls = []

    def __call__(self, *command, env=None, cwd=None):
        if command[0] == "git":
            result = subprocess.run(command, capture_output=True, text=True, env={**os.environ, **(env or {})})
            return result.returncode, result.stdout + result.stderr
        self.calls.append(command)
        for size in range(len(command), 0, -1):
            if tuple(command[:size]) in self.answers:
                return self.answers[tuple(command[:size])]
        if command[:1] == ("sudo",) and command[1:2] == ("mktemp",):
            return 0, self.restore_dir + "\n"
        return 0, ""

    def ran(self, *words):
        return [c for c in self.calls if tuple(c[:len(words)]) == words]

    restore_dir = "/tmp/restore"


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.github, self.config_github = f"{t}/github", f"{t}/config-github"
        new_repo(self.github)
        commit(self.github, "provision.yml", "v1", "first")
        git(self.github, "tag", "v1.0.0")
        new_repo(self.config_github)
        commit(self.config_github, "settings.yml", "a: 1", "first config")
        # The server's copy and your config repo inside it, both cloned from those.
        self.repo = f"{t}/server-copy"
        subprocess.run(["git", "clone", "-q", self.github, self.repo], check=True)
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        self.config = f"{self.repo}/config"
        subprocess.run(["git", "clone", "-q", self.config_github, self.config], check=True)
        git(self.config, "config", "user.email", "t@example.com")
        git(self.config, "config", "user.name", "t")
        self.state = f"{t}/state"
        os.makedirs(self.state)
        self.stack = f"{t}/media-stack"
        os.makedirs(self.stack)
        self.env = {
            "HOMELAB_REPO_DIR": self.repo, "HOMELAB_STATE_DIR": self.state, "HOMELAB_REPO_URL": "https://example.com/repo",
            "HOMELAB_SSH_KEY": "/nonexistent", "HOMELAB_STACK_DIRS": f"{self.stack} {t}/not-installed",
            "HOMELAB_DATA_DIR": f"{t}/data", "PATH": os.environ["PATH"], "HOME": t,
        }
        self.fake, self.sent = Fake(), Sent()
        self.fake.answers[("pgrep",)] = (1, "")   # no playbook running
        self.deploy = homelab_deploy.Deploy(self.env, run=self.fake, telegram=self.sent)
        self.addCleanup(self.tmp.cleanup)

    def go(self):
        with contextlib.redirect_stdout(io.StringIO()):   # the deploy prints Ansible's output for the journal
            return homelab_deploy.Deploy(self.env, run=self.fake, telegram=self.sent).main()

    def playbook_runs(self):
        return self.fake.ran(".venv/bin/ansible-playbook")

    def said(self, text):
        return any(text in message for message in self.sent.messages)


class Helpers(unittest.TestCase):
    def test_version_shows_a_release_or_commits_since_it(self):
        self.assertEqual(homelab_deploy.version("v1.1.0"), "v1.1.0")
        self.assertEqual(homelab_deploy.version("v1.1.0-3-g1a2b3c4"), "v1.1.0 + 3 commits")

    def test_the_failure_is_the_task_above_the_fatal_line_and_its_message(self):
        task, error = homelab_deploy.failure_of(ANSIBLE_FAILURE)
        self.assertEqual(task, "[app_config : Seerr | Telegram notifications]")
        self.assertIn("POST http://127.0.0.1:5055/api/v1/settings/notifications/telegram failed:", error)
        self.assertIn("HTTP 500", error)

    def test_without_a_fatal_line_the_error_is_the_end_of_the_log(self):
        task, error = homelab_deploy.failure_of("one\ntwo\nthree\nfour\n")
        self.assertEqual((task, error), ("", "two\nthree\nfour"))

    def test_text_is_escaped_for_telegram_html(self):
        self.assertEqual(homelab_common.escape('a <b> & "c"'), 'a &lt;b&gt; &amp; "c"')

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


class StateNotes(Case):
    def test_a_failure_is_remembered_with_when_commit_task_and_error(self):
        state = homelab_common.State(self.state, self.repo)
        state.fail("abc def", "2026-11-25 20:00", "the task", "the error")
        self.assertEqual(state.failed(), "abc def")
        self.assertEqual(state.last_failure(), ("2026-11-25 20:00", "abc", "the task\nthe error"))
        state.succeed()
        self.assertIsNone(state.failed())
        self.assertIsNotNone(state.last_failure())   # /log still shows the last failure, as "fixed later"

    def test_the_hold_and_a_request_for_main(self):
        state = homelab_common.State(self.state, self.repo)
        self.assertIsNone(state.held())
        state.hold("held from Telegram")
        self.assertEqual(state.held()[0], "held from Telegram")
        self.assertFalse(state.take_request_for_main())
        state.ask_for_main()
        self.assertTrue(state.take_request_for_main())
        self.assertIsNone(state.held())               # /main lifts the hold
        self.assertFalse(state.take_request_for_main())


class Deploys(Case):
    def test_nothing_new_does_nothing(self):
        self.assertEqual(self.go(), 0)
        self.assertEqual(self.playbook_runs(), [])
        self.assertEqual(self.sent.messages, [])

    def test_a_new_commit_is_deployed_and_reported(self):
        new = commit(self.github, "provision.yml", "v2", "a fix <here>")
        self.assertEqual(self.go(), 0)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), new)
        self.assertEqual([c[0] for c in self.fake.calls if c[0].startswith(".venv")],
                         [".venv/bin/pip", ".venv/bin/ansible-galaxy", ".venv/bin/ansible-playbook"])
        self.assertEqual(self.fake.ran("restic"), [])        # no new images: no backup
        self.assertTrue(self.said("Updating"))
        self.assertTrue(self.said("a fix &lt;here&gt;"))     # the title is escaped, with a link to the commit
        self.assertTrue(self.said(f"https://example.com/repo/commit/{new}"))
        self.assertTrue(self.said("Updated to"))
        self.assertIsNone(homelab_common.State(self.state, self.repo).failed())

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
        commit(self.github, "provision.yml", "v2")
        self.fake.answers[(".venv/bin/ansible-playbook",)] = (2, ANSIBLE_FAILURE)
        self.assertEqual(self.go(), 1)
        runs = len(self.playbook_runs())
        self.assertEqual(self.go(), 0)                        # the same commits: not tried again
        self.assertEqual(len(self.playbook_runs()), runs)
        commit(self.github, "provision.yml", "v3", "the fix")
        self.fake.answers.pop((".venv/bin/ansible-playbook",))
        self.assertEqual(self.go(), 0)
        self.assertTrue(self.said("Updated to"))


class WithoutConfigRepo(Case):
    def test_a_failed_commit_is_also_remembered_when_there_is_no_config_repo(self):
        shutil.rmtree(self.config)
        commit(self.github, "provision.yml", "v2")
        self.fake.answers[(".venv/bin/ansible-playbook",)] = (2, ANSIBLE_FAILURE)
        self.assertEqual(self.go(), 1)
        runs = len(self.playbook_runs())
        self.assertEqual(self.go(), 0)
        self.assertEqual(len(self.playbook_runs()), runs)


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
        backup = self.fake.ran("sudo", "--preserve-env=RESTIC_REPOSITORY,RESTIC_PASSWORD", "restic", "backup")[0]
        self.assertIn("pre-deploy", backup)
        self.assertIn("*/prometheus/data", backup)             # what is left out of every backup
        self.assertIn(self.stack, backup)
        self.assertNotIn(f"{self.tmp.name}/not-installed", backup)   # a stack that is not installed is skipped
        self.assertEqual([c[-1] for c in self.fake.ran("docker", "compose")], ["stop", "start"])
        self.assertTrue(self.fake.ran("sudo", "--preserve-env=RESTIC_REPOSITORY,RESTIC_PASSWORD", "restic", "forget"))

    def test_a_failed_backup_skips_the_update(self):
        self.image_update()
        old = git(self.repo, "rev-parse", "HEAD")
        self.fake.answers[("sudo", "--preserve-env=RESTIC_REPOSITORY,RESTIC_PASSWORD", "restic", "backup")] = (1, "")
        self.assertEqual(self.go(), 1)
        self.assertEqual(self.playbook_runs(), [])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), old)
        self.assertEqual([c[-1] for c in self.fake.ran("docker", "compose")], ["stop", "start"])   # the apps run again
        self.assertTrue(self.said("Update skipped"))
        self.assertIsNotNone(homelab_common.State(self.state, self.repo).failed())

    def test_a_failed_deploy_after_a_backup_restores_it_and_deploys_the_previous_commit(self):
        old = git(self.repo, "rev-parse", "HEAD")
        self.image_update()
        results = iter([(2, ANSIBLE_FAILURE), (0, "")])
        original = self.fake.__call__

        def playbook_fails_once(*command, env=None, cwd=None):
            if command[:1] == (".venv/bin/ansible-playbook",):
                self.fake.calls.append(command)
                return next(results)
            return original(*command, env=env, cwd=cwd)
        self.deploy.run = playbook_fails_once
        deploy = homelab_deploy.Deploy(self.env, run=playbook_fails_once, telegram=self.sent)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(deploy.main(), 1)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), old)                     # back on the previous commit
        self.assertTrue(self.fake.ran("sudo", "--preserve-env=RESTIC_REPOSITORY,RESTIC_PASSWORD", "restic", "restore"))
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
        self.fake.answers[(".venv/bin/ansible-playbook",)] = (2, ANSIBLE_FAILURE)
        self.assertEqual(self.go(), 1)
        self.assertEqual(self.fake.ran("sudo", "--preserve-env=RESTIC_REPOSITORY,RESTIC_PASSWORD", "restic", "restore"), [])
        self.assertTrue(self.said("Rollback failed too"))   # the fake playbook fails again, also on the previous commit

    def test_a_failing_config_change_is_left_for_you_to_fix(self):
        commit(self.config_github, "settings.yml", "a: 2", "a bad setting")
        self.fake.answers[(".venv/bin/ansible-playbook",)] = (2, ANSIBLE_FAILURE)
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
        self.assertEqual(len([m for m in self.sent.messages if "still on hold" in m]), 1)

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

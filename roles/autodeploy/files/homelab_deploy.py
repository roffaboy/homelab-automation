#!/usr/bin/env python3
"""Runs the playbook when main on GitHub has new commits, or your config repo (config/) has.

Before new images it backs up the app data; when the run fails it restores that backup and deploys
the previous commit again. A failing config change is left for you to fix. Started by
homelab-autodeploy.timer (or by the bot's /deploy and /main); the full output is in:
journalctl -u homelab-autodeploy

Everything it needs comes from the environment (homelab-autodeploy.service), so the same file runs
in the tests (tests/test_homelab_deploy.py), where the commands that need the server are replaced.
"""

import fcntl
import os
import re
import subprocess
import sys
import tempfile
import time

from homelab_common import State, Telegram, escape

# Left out of every backup: Prometheus' measurements, and what the apps download again (Grafana's
# plugins, Recyclarr's guides). Calibre-Web-Automated's zips of the books as they were imported are
# copies of the library.
SKIP = ("prometheus/data", "grafana/data/plugins", "recyclarr/resources", "calibre/processed_books")
RUNNING_PLAYBOOK = r"^[^ ]*python[^ ]* [^ ]*/ansible-playbook( |$)"


def version(describe):
    """The release a commit belongs to: v1.1.0, or v1.1.0 + 3 commits since that tag."""
    return re.sub(r"-(\d+)-g[0-9a-f]+$", r" + \1 commits", describe)


def failure_of(log):
    """(task, error) of the first failure in Ansible's output: the task above its "fatal:" or
    "failed:" line, and its msg: with the indented lines that continue it, else the end of the log."""
    task, error, lines = "", "", log.splitlines()
    for number, line in enumerate(lines):
        if line.startswith("TASK ["):
            task = re.sub(r" \*+$", "", line[len("TASK "):])
        if not re.match(r"(fatal|failed):", line):
            continue
        message, depth = [], None
        for following in lines[number + 1:]:
            indent = len(following) - len(following.lstrip(" "))
            if depth is None:
                if re.match(r" +msg:", following):
                    depth = indent
                    message.append(re.sub(r"^ +msg: *", "", following))
                continue
            if indent > depth:
                message.append(following.strip())
            else:
                break
        error = " ".join(message).strip("'")
        break
    else:
        task = ""
    return task, error or "\n".join(lines[-3:])


class Deploy:
    def __init__(self, env, run=None, telegram=None):
        self.env = env
        self.repo = env["HOMELAB_REPO_DIR"]
        self.web = env["HOMELAB_REPO_URL"]
        self.state = State(env["HOMELAB_STATE_DIR"], self.repo)
        self.telegram = telegram or Telegram(env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID"))
        self.run = run or self._run
        stacks = [d for d in env["HOMELAB_STACK_DIRS"].split() if os.path.isdir(d)]
        books = os.path.join(env["HOMELAB_DATA_DIR"], "media", "books")
        self.stacks = stacks
        self.backup_dirs = stacks + ([books] if os.path.isdir(books) else [])
        self.started = time.monotonic()

    # --- the machine ---------------------------------------------------------------------------

    def _run(self, *command, env=None, cwd=None):
        """(exit code, output) of a command; the output is stdout, then stderr."""
        result = subprocess.run(command, capture_output=True, text=True, env=env or self.env, cwd=cwd)
        return result.returncode, result.stdout + result.stderr

    def git(self, *args, repo=None, env=None):
        return self.run("git", "-C", repo or self.repo, *args, env=env)

    def compose(self, action):
        for stack in self.stacks:
            self.run("docker", "compose", "--project-directory", stack, action)
            if action == "start":
                self.wait_until_started(stack)

    def wait_until_started(self, stack, seconds=180):
        """Stacks start in order (downloads first); wait until its containers are through their health
        check, so the next stack does not look for them too early (Sonarr then reports qBittorrent
        as down). Gives up after `seconds`: an app that stays unhealthy must not hold up the rest."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            status = self.run("docker", "ps", "--filter", f"label=com.docker.compose.project.working_dir={stack}",
                              "--format", "{{.Status}}")[1]
            if "health: starting" not in status:
                return
            time.sleep(3)

    def restic(self, *args):
        return self.run("sudo", "--preserve-env=RESTIC_REPOSITORY,RESTIC_PASSWORD", "restic", *args)

    def notify(self, text):
        self.telegram.send(text)

    # --- one pass --------------------------------------------------------------------------------

    def deploy_playbook(self, log):
        """pip, the collections, the playbook; the output is added to log. True when all went well."""
        for command in (
            (".venv/bin/pip", "install", "-q", "-r", "requirements.txt"),
            (".venv/bin/ansible-galaxy", "collection", "install", "-r", "collections/requirements.yml"),
            (".venv/bin/ansible-playbook", "provision.yml"),
        ):
            status, output = self.run(*command, cwd=self.repo)
            log.append(output)
            if status:
                return False
        return True

    def changes(self, old, new, config_old, config_new):
        """One line per commit: a link to it on GitHub, then its title."""
        lines = []
        for entry in self.git("log", "--format=%H %h %s", f"{old}..{new}")[1].splitlines():
            full, short, title = entry.split(" ", 2)
            lines.append(f'• <a href="{self.web}/commit/{full}">{short}</a> {escape(title)}')
        if config_old != config_new:
            for entry in self.git("log", "--format=%h %s", f"{config_old}..{config_new}", repo=f"{self.repo}/config")[1].splitlines():
                short, title = entry.split(" ", 1)
                lines.append(f"• config {short} {escape(title)}")
        return "\n".join(lines)

    def hold_or_force(self):
        """None to go on, or "hold" to leave the server as it is. Returns (go, force)."""
        if self.state.take_request_for_main():
            return True, True
        held = self.state.held()
        if held:
            why, since = held
            day = time.strftime("%F", time.localtime(since))
            if day != time.strftime("%F") and not self.state.reminded_today():
                self.state.remind()
                self.notify(f"✋ Auto-deploys still on hold since {time.strftime('%-d %b', time.localtime(since))}: "
                            f"{escape(why)}. /main puts main back.")
            return False, False
        return True, False

    def fetch(self):
        """(old, new, config_old, config_new): the commits of this copy and of GitHub, for main and for
        your config repo, or None when GitHub can't be reached."""
        ssh = {**self.env, "GIT_SSH_COMMAND": f"ssh -i {self.env['HOMELAB_SSH_KEY']} -o IdentitiesOnly=yes"}
        if self.git("fetch", "--quiet", "--tags", "origin", "main", env=ssh)[0]:
            return None
        new = self.git("rev-parse", "FETCH_HEAD")[1].strip()
        old = self.git("rev-parse", "HEAD")[1].strip()
        config = f"{self.repo}/config"
        config_old = self.git("rev-parse", "HEAD", repo=config)[1].strip() if os.path.isdir(config) else ""
        config_new = config_old
        # Your config repo is fetched with your own SSH key: a deploy key fits one repo only.
        own = {k: v for k, v in self.env.items() if k != "GIT_SSH_COMMAND"}
        if config_old and self.git("fetch", "--quiet", repo=config, env=own)[0] == 0:
            upstream = self.git("rev-parse", "@{upstream}", repo=config)
            config_new = upstream[1].strip() if upstream[0] == 0 else config_old
            # A commit you have not pushed yet is ahead of GitHub, not new: do not deploy for it every time.
            if self.git("merge-base", "--is-ancestor", config_new, config_old, repo=config)[0] == 0:
                config_new = config_old
        return old, new, config_old, config_new

    def back_up(self):
        """The apps stop while their data is backed up, so the databases are consistent. True when it worked."""
        if self.restic("cat", "config")[0]:
            self.restic("init")
        self.compose("stop")
        excludes = [arg for path in SKIP for arg in ("--exclude", f"*/{path}")]
        status = self.restic("backup", "--tag", "pre-deploy", *excludes, *self.backup_dirs)[0]
        self.compose("start")
        if status == 0:
            self.restic("forget", "--prune", "--tag", "pre-deploy", "--keep-last", "5")
        return status == 0

    def roll_back_data(self):
        """The app data from the backup taken before this deploy."""
        self.compose("stop")
        directory = self.run("sudo", "mktemp", "-d")[1].strip()
        if self.restic("restore", "latest", "--tag", "pre-deploy", "--target", directory)[0] == 0:
            excludes = [arg for path in SKIP for arg in ("--exclude", path)]
            for backed_up in self.backup_dirs:
                self.run("sudo", "rsync", "-a", "--delete", *excludes, f"{directory}{backed_up}/", f"{backed_up}/")
        self.run("sudo", "rm", "-rf", directory)

    def main(self):
        go, force = self.hold_or_force()
        if not go:
            return 0
        fetched = self.fetch()
        if fetched is None:
            return 1
        old, new, config_old, config_new = fetched
        commits = f"{new} {config_new}".strip()   # without a config repo there is only the main commit
        if not force:
            if new == old and config_new == config_old:
                return 0
            # A commit that failed waits for the next one (a fix, or a revert of the PR).
            if commits == self.state.failed():
                return 0
        # A run you started by hand goes first; the next check picks this up.
        if self.run("pgrep", "-f", RUNNING_PLAYBOOK)[0] == 0:
            return 0

        changes = self.changes(old, new, config_old, config_new) or ("• back to main" if force else "")
        self.notify(f"🔄 <b>Updating</b>\n{changes}")

        # Only new images can change the app data, so only then back up.
        backup = self.git("diff", "--quiet", "HEAD", "FETCH_HEAD", "--", "roles/*_stack/defaults/main.yml")[0] != 0
        if backup and not self.back_up():
            self.state.fail(commits, time.strftime("%F %R"), "The backup failed", "")
            self.notify("❌ <b>Update skipped</b>\nBackup failed\njournalctl -u homelab-autodeploy")
            return 1

        self.git("reset", "--quiet", "--hard", new)
        # Fast-forward only: config/ is your own folder, so changes you haven't committed stay.
        if config_new != config_old and self.git("merge", "--quiet", "--ff-only", config_new, repo=f"{self.repo}/config")[0]:
            self.state.fail(commits, time.strftime("%F %R"), "Uncommitted changes in ~/homelab-config", "")
            self.notify("❌ <b>Update stopped</b>\nUncommitted changes in ~/homelab-config")
            return 1

        log = []
        if self.deploy_playbook(log):
            self.report(log)
            self.state.succeed()
            seconds = int(time.monotonic() - self.started)
            described = version(self.git("describe", "--tags", "--always", "HEAD")[1].strip())
            self.notify(f'✅ <b>Updated to <a href="{self.web}/commit/{new}">{escape(described)}</a></b> in {seconds // 60}m {seconds % 60}s')
            return 0

        self.report(log)
        task, error = failure_of("".join(log))
        error = error if len(error) <= 300 else error[:300] + "…"
        self.state.fail(commits, time.strftime("%F %R"), task or "?", error)

        if backup:
            self.roll_back_data()
        message = f"❌ <b>Update failed</b>\n{escape(task or 'see the log')}\n{escape(error)}\n{changes}\n"
        if new == old:
            self.notify(message + "Fix your config repo\njournalctl -u homelab-autodeploy")
            return 1
        self.git("reset", "--quiet", "--hard", old)
        result = "Rolled back" if self.deploy_playbook([]) else "Rollback failed too"
        self.notify(message + f"{result}: fix or revert the PR\njournalctl -u homelab-autodeploy")
        return 1

    @staticmethod
    def report(log):
        """The output, for the journal."""
        sys.stdout.write("".join(log))
        sys.stdout.flush()


def open_lock(path):
    """One at a time with the backups (they wait for this lock, a deploy skips its check while they hold
    it). Returns the open file, or None when the lock is taken."""
    if not os.path.exists(path):
        open(path, "a").close()
    lock = open(path)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return None
    return lock


def main():
    env = dict(os.environ)
    lock = open_lock(env.get("HOMELAB_LOCK", "/run/lock/homelab.lock"))
    if lock is None:
        return 0
    return Deploy(env).main()


if __name__ == "__main__":
    tempfile.tempdir = None
    sys.exit(main())

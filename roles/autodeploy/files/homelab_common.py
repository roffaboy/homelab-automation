"""What the deploys (homelab-autodeploy) and the Telegram bot (homelab-bot) share: the messages sent to
Telegram, and the files in which they leave each other notes. Installed next to them in
/usr/local/lib/homelab/ and tested in tests/test_homelab_deploy.py.
"""

import html
import json
import os
import time
import urllib.request


def escape(text):
    """Text that is not markup, for a Telegram HTML message."""
    return html.escape(text, quote=False)


class Telegram:
    """One bot talking to one chat. Without a token it does nothing, so the deploys also work without one."""

    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id

    def api(self, method, wait=10, **params):
        request = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}",
                                         json.dumps(params).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=wait) as response:
            return json.load(response)["result"]

    def send(self, text, **extra):
        """A message: a bold first line with an icon, the details, then where to look. Never raises."""
        if not self.token:
            return
        try:
            self.api("sendMessage", wait=20, chat_id=self.chat_id, text=text, parse_mode="HTML",
                     link_preview_options={"is_disabled": True}, **extra)
        except Exception as error:  # a message that is not delivered must not stop a deploy
            print(f"Telegram: {type(error).__name__}", flush=True)  # not the error itself: its URL holds the token


class State:
    """The notes the deploy and the bot leave each other: the hold on automatic deploys (in the state
    folder), and the failed commit and the last failure (in the folder of the repo)."""

    def __init__(self, state_dir, repo_dir):
        self.state_dir = state_dir
        self.repo_dir = repo_dir

    def _path(self, name, in_repo=False):
        return os.path.join(self.repo_dir if in_repo else self.state_dir, name)

    def _read(self, name, in_repo=False):
        try:
            with open(self._path(name, in_repo)) as file:
                return file.read()
        except FileNotFoundError:
            return None

    def _write(self, name, text, in_repo=False):
        with open(self._path(name, in_repo), "w") as file:
            file.write(text)

    def _remove(self, *names):
        for name in names:
            try:
                os.remove(self._path(name))
            except FileNotFoundError:
                pass

    # The hold: the server keeps what it runs. /main (the bot) asks for a deploy of main, which lifts it.
    def hold(self, why):
        self._write("hold", why + "\n")

    def held(self):
        """(why, since as a time stamp) when on hold, else None."""
        why = self._read("hold")
        return None if why is None else (why.strip(), os.path.getmtime(self._path("hold")))

    def ask_for_main(self):
        self._write("deploy-main", "")

    def take_request_for_main(self):
        """True once when /main was sent; this also lifts the hold."""
        if self._read("deploy-main") is None:
            return False
        self._remove("deploy-main", "hold")
        return True

    def reminded_today(self):
        return self._read("hold-reminded") == time.strftime("%F\n")

    def remind(self):
        self._write("hold-reminded", time.strftime("%F\n"))

    # A commit that failed waits for the next one: "<main commit> <config commit>".
    def failed(self):
        text = self._read(".failed-commit", in_repo=True)
        return None if text is None else text.strip()

    def fail(self, commits, when, task, error):
        self._write(".failed-commit", commits + "\n", in_repo=True)
        self._write(".last-failure", "\n".join([when, commits.split()[0], task, error]) + "\n", in_repo=True)

    def succeed(self):
        try:
            os.remove(self._path(".failed-commit", in_repo=True))
        except FileNotFoundError:
            pass

    def last_failure(self):
        """(when, commit, text) of the last failed deploy, else None."""
        text = self._read(".last-failure", in_repo=True)
        if text is None:
            return None
        when, commit, *what = text.strip().split("\n")
        return when, commit, "\n".join(what)

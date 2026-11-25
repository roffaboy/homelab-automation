"""Tests of the Telegram bot's reading of Docker (roles/autodeploy/files/homelab-bot.py)."""
import importlib.util
import os
import unittest

FILES = os.path.join(os.path.dirname(__file__), "..", "roles", "autodeploy", "files")


def load_bot():
    os.environ.update({"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "1", "HOMELAB_REPO_DIR": "/tmp", "HOMELAB_STATE_DIR": "/tmp",
                       "HOMELAB_REPO_URL": "https://example.com/repo", "HOMELAB_DATA_DIR": "/tmp", "HOMELAB_LIB": FILES})
    spec = importlib.util.spec_from_file_location("homelab_bot", os.path.join(FILES, "homelab-bot.py"))
    bot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bot)
    return bot


class Containers(unittest.TestCase):
    def test_a_container_that_compose_did_not_start_is_listed_too(self):
        bot = load_bot()
        # What docker prints: the first line (sorted) has empty labels, so it starts with tabs.
        output = "\t\tsandbox\tUp 3 minutes\nmedia-stack\tradarr\tmedia-stack-radarr-1\tUp 2 hours (healthy)\n" \
                 "media-stack\tseerr\tmedia-stack-seerr-1\tUp 2 hours (unhealthy)\n"
        bot.run = lambda *command, strip=True: output if not strip else output.strip()
        found = list(bot.containers())
        self.assertEqual(found[0], ("Other", "sandbox", "Up 3 minutes", True))
        self.assertEqual([(stack, app, ok) for stack, app, _, ok in found[1:]], [("Media", "radarr", True), ("Media", "seerr", False)])


if __name__ == "__main__":
    unittest.main()

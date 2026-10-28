"""Prepare Hostpoint credentials interactively; never changes a running service."""

import getpass
import os
from pathlib import Path

if os.geteuid() != 0 or os.uname().nodename != "ubuntu-4gb-fsn1-1":
    raise SystemExit("Run with sudo on the archive/Staging host only")
root = Path("/etc/greenmind/auth-staging-20261028")
if root.exists():
    raise SystemExit("Staging credential directory already exists; review before replacing")
password = getpass.getpass("Hostpoint-Mailboxpasswort für info@green-mind.ch (verdeckt): ")
if not password or password != getpass.getpass("Passwort wiederholen (verdeckt): "):
    raise SystemExit("No credential saved: passwords missing or different")
if "\n" in password or "\r" in password:
    raise SystemExit("Invalid mailbox password")
os.umask(0o077)
root.mkdir(parents=True, mode=0o710)
os.chown(root, 0, 10001)
os.chmod(root, 0o710)
path = root / "smtp-password"
fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o640)
with os.fdopen(fd, "w") as output:
    output.write(password)
os.chown(path, 0, 10001)
os.chmod(path, 0o640)
env = root / "mail.env"
fd = os.open(env, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "w") as output:
    output.write("EMAIL_TRANSPORT=smtp\nSMTP_HOST=asmtp.mail.hostpoint.ch\nSMTP_PORT=465\nSMTP_USERNAME=info@green-mind.ch\nEMAIL_FROM=info@green-mind.ch\nSMTP_PASSWORD_FILE=/run/secrets/greenmind-smtp-password\nSTAGING_EMAIL_TO=traver.dinten@outlook.com\n")
print("Staging SMTP credential prepared. No service changed; no email sent.")

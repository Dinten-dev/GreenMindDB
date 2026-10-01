"""File-size limit in a fresh process, safe even when caller has threads."""

import os
import resource
import sys


def main():
    maximum = int(sys.argv[1])
    if not 0 < maximum <= 64 * 1024**2 or sys.argv[2] != '/usr/bin/sftp':
        raise SystemExit('Invalid bounded SFTP invocation')
    resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))
    os.execv('/usr/bin/sftp', sys.argv[2:])  # noqa: S606 - fixed binary, no shell


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Collect missing Bot tokens over an interactive SSH terminal, without echo."""
import argparse
import getpass
import os
import sys
import tempfile
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from discord_party.installation import outside_repository


def write_token(directory: Path, agent: str, token: str):
    if agent not in ('agent_a', 'agent_b'):
        raise ValueError('Unknown agent')
    if not token or len(token) > 4096 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError('Invalid token format')
    if directory.is_symlink():
        raise ValueError('Secret directory must not be a symlink')
    directory = outside_repository(directory)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if directory.stat().st_mode & 0o077:
        raise PermissionError('Secret directory must be mode 700')
    target = directory / f'{agent}.token'
    fd, temporary = tempfile.mkstemp(prefix='.incoming-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as handle:
            handle.write(token + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        # Atomic no-overwrite publication; existing files and symlinks both fail.
        os.link(temporary, target)
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        os.unlink(temporary)


def collect(directory: Path):
    print('依提示貼入 Bot token 後按 Enter；輸入不會顯示，也不會印出。')
    for agent, label in [('agent_a', 'Agent A'), ('agent_b', 'Agent B')]:
        target = directory / f'{agent}.token'
        if target.exists() or target.is_symlink():
            print(f'{label}：檔案已存在，保留原檔。')
            continue
        with warnings.catch_warnings():
            # getpass normally falls back to echoed stdin when there is no terminal.
            # Abort before reading anything if the hidden-input path is unavailable.
            warnings.simplefilter('error', getpass.GetPassWarning)
            token = getpass.getpass(f'{label} Bot token：')
        write_token(directory, agent, token.strip())
        del token
        print(f'{label}：已存入本機權限 600 的私密檔案；尚未驗證登入。')
    print('完成。回覆「token 放好了」，即可接續驗證與啟動。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--secrets-dir', type=Path,
                        default=Path.home()/'Library/Application Support/kc-agent-party/secrets')
    args = parser.parse_args()
    collect(args.secrets_dir)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 - never print secret-bearing exceptions
        print(f'未完成：{type(error).__name__}。請使用互動式 SSH 終端，保留已存檔案。', file=sys.stderr)
        sys.exit(1)

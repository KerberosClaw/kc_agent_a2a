#!/usr/bin/env python3
"""Bind a CLI to explicit config; never install hooks or start native sessions."""
import argparse
import json
import os
from pathlib import Path
import sys

repo=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(repo/'src'))
from a2a.native_message import validate_config, load_config
from a2a.storage import atomic_write


def install(config, helper):
    if config.is_symlink():raise ValueError('symlinked config')
    config=config.resolve()
    if config.is_symlink() or config.stat().st_mode & 0o077:
        raise ValueError('config requires mode 600')
    value=load_config(config);validate_config(value)
    if helper.is_symlink():raise ValueError('symlinked helper')
    if helper.exists():
        prior=helper.read_text()
        if 'from a2a.native_message import main' not in prior:
            raise ValueError('helper is occupied by another program')
        backup=helper.with_name(helper.name+'.before-native-update')
        if backup.exists():raise ValueError('inspect previous helper backup before updating')
        atomic_write(backup,prior)
    helper.parent.mkdir(parents=True,exist_ok=True)
    text=('#!'+sys.executable+'\nimport os,sys\nos.umask(0o077)\nsys.dont_write_bytecode=True\n'
          +'sys.path.insert(0,'+repr(str(repo/'src'))+')\nfrom a2a.native_message import main\n'
          +'sys.argv=[sys.argv[0],"--config",'+repr(str(config))+',*sys.argv[1:]]\nmain()\n')
    atomic_write(helper,text);helper.chmod(0o700)
    return {'helper':str(helper),'config':str(config),'hooks_modified':False,'sessions_started':False}


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--helper',type=Path,default=Path.home()/'.local/bin/agent-message')
    args=parser.parse_args()
    print(json.dumps(install(args.config,args.helper.absolute())))


if __name__=='__main__':main()

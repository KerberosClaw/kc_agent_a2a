#!/usr/bin/env python3
"""Explicit first-install, local status and synthetic Gateway probe."""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from discord_party.installation import bind_bot, outside_repository, read_status
from discord_party.state import Registry, State


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('init', 'status', 'run-synthetic', 'run-native'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--token-file', type=Path)
    parser.add_argument('--grant', type=Path)
    parser.add_argument('--agent', choices=('agent_a', 'agent_b'))
    parser.add_argument('--native-work', type=Path)
    parser.add_argument('--memory', type=Path)
    parser.add_argument('--shared-view', type=Path)
    parser.add_argument('--experience-memory', type=Path)
    parser.add_argument('--life-context-policy', type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    config['human_ids'] = tuple(config['human_ids'])
    registry = Registry(**config)
    state_path = outside_repository(args.state)
    if args.command == 'status':
        print(json.dumps(read_status(state_path, registry), ensure_ascii=False, indent=2))
        return
    bindings = Path.home()/'Library/Application Support/kc-agent-party/bindings'
    with bind_bot(bindings, registry, state_path, initialize=args.command == 'init'):
        state = State(state_path, registry, initialize=args.command == 'init')
        try:
            if args.command in ('run-synthetic', 'run-native'):
                if args.token_file is None:
                    parser.error('--token-file is required')
                from discord_party.connector import PartyClient, read_token
                # Preserve the original path so read_token can reject a symlink.
                outside_repository(args.token_file)
                token = read_token(args.token_file)
                engine = None
                if args.command == 'run-native':
                    if not all((args.grant, args.agent, args.native_work)):
                        parser.error('--grant, --agent and --native-work are required')
                    from discord_party.native import Grant, NativeEngine
                    from discord_party.life_context import LifeContextResolver
                    grant = Grant(outside_repository(args.grant), args.agent, registry)
                    life_context = (LifeContextResolver(outside_repository(args.life_context_policy), registry)
                                    if args.life_context_policy else None)
                    engine = NativeEngine(grant, outside_repository(args.native_work),
                                          memory=outside_repository(args.memory) if args.memory else None,
                                          shared_view=outside_repository(args.shared_view) if args.shared_view else None,
                                          life_context=life_context)
                    engine.experience_memory = outside_repository(args.experience_memory) if args.experience_memory else None
                    state.set_grant(grant.version)
                client = PartyClient(state, token, engine=engine)
                logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s %(message)s')
                logging.getLogger('discord').setLevel(logging.CRITICAL)
                client.run(token, log_handler=None)
            else:
                print(json.dumps(state.snapshot(), ensure_ascii=False, indent=2))
        finally:
            state.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:  # noqa: BLE001 - redact all CLI failures
        # Error class only; no credentials, HTTP payloads or public chat body.
        print(f'NOT_READY: {type(error).__name__}; check local configuration and state', file=sys.stderr)
        sys.exit(1)

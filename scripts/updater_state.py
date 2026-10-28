"""Public, repository-backed update state, isolated by platform/architecture/channel."""
import json

from release_metadata import CHANNELS, VERSION, validate_target

COHORT_FIELDS = ('cohort', 'cohorthint', 'cohortname')


def validate_entry(key, entry):
    parts = key.split('/')
    if len(parts) != 3:
        raise ValueError(f'Invalid updater target: {key}')
    platform, arch, channel = parts
    validate_target(platform, arch)
    if channel not in CHANNELS or (platform == 'linux' and channel != 'stable'):
        raise ValueError(f'Invalid updater channel: {key}')
    if not isinstance(entry, dict) or set(entry) - {'version', *COHORT_FIELDS}:
        raise ValueError(f'Invalid updater state: {key}')
    version = entry.get('version')
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError(f'Invalid updater version: {key}')
    for field in COHORT_FIELDS:
        if field in entry:
            value = entry[field]
            if not isinstance(value, str) or len(value) > 1024 or any(not 32 <= ord(c) <= 126 for c in value):
                raise ValueError(f'Invalid updater cohort field: {key}/{field}')


def read_state(path):
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or value.get('schema_version') != 1
            or set(value) != {'schema_version', 'targets'} or not isinstance(value.get('targets'), dict)):
        raise ValueError(f'Invalid updater state file: {path}')
    for key, entry in value['targets'].items():
        validate_entry(key, entry)
    return value['targets']


def write_state(path, targets):
    value = {'schema_version': 1, 'targets': dict(sorted(targets.items()))}
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def merge_discovery(targets, root):
    """Merge successful run candidates without allowing stale jobs to lower a version."""
    merged = {key: dict(entry) for key, entry in targets.items()}
    for path in sorted(root.glob('*-discovery.json')):
        value = json.loads(path.read_text(encoding='utf-8'))
        for channel, entry in value.get('updater_state', {}).items():
            key = f'{value["platform"]}/{value["architecture"]}/{channel}'
            validate_entry(key, entry)
            if value['channels'].get(channel) != entry['version']:
                raise ValueError(f'Updater state does not match discovery: {path}/{channel}')
            previous = merged.get(key, {})
            if tuple(map(int, entry['version'].split('.'))) >= tuple(map(int, previous.get('version', '0.0.0.0').split('.'))):
                merged[key] = {**previous, **entry}
    return merged


def state_versions(targets):
    result = {}
    for key, entry in targets.items():
        platform, arch, channel = key.split('/')
        if entry['version'] != '0.0.0.0':
            result.setdefault((platform, arch), {})[channel] = entry['version']
    return result

"""Build the wheel's init payload from the canonical repository resources."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def payload():
    paths = [ROOT / p for p in (
        'ontology/core.ttl', 'ontology/agent-memory.ttl',
        'shapes/core.shapes.ttl', 'shapes/agent-memory.shapes.ttl',
        'shapes/observation-debt.shapes.ttl')]
    paths += sorted((ROOT / 'queries').glob('*.json'))
    return {str(p.relative_to(ROOT)): p.read_text() for p in paths}


if __name__ == '__main__':
    (ROOT / 'camayoc_cli/data/init-resources.json').write_text(
        json.dumps(payload(), indent=2, sort_keys=True) + '\n')

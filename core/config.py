"""Client config loading and validation. A client is configuration, not code."""
import json
from pathlib import Path
import jsonschema, yaml

SCHEMA = json.loads((Path(__file__).parent.parent / "config" / "client.schema.json").read_text())

class ConfigError(ValueError):
    pass

def load_client(path):
    cfg = yaml.safe_load(Path(path).read_text())
    errors = sorted(jsonschema.Draft202012Validator(SCHEMA).iter_errors(cfg), key=lambda e: list(e.path))
    if errors:
        raise ConfigError("; ".join(f"{'.'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors))
    return cfg

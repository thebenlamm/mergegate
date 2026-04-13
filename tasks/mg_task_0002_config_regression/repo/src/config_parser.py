"""Config file parser supporting [section] key=value format."""


def parse_config(text: str) -> dict:
    """Parse a config file string into a dictionary.

    Supports:
    - Flat key=value pairs (no section header)
    - [section] headers with key=value pairs beneath

    Returns a dict. Flat keys are top-level. Sectioned keys are
    nested: {"section": {"key": "value"}}.
    """
    result = {}
    current_section = None

    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("[") and line.endswith("]"):
            section_name = line[1:-1].strip()
            current_section = section_name
            result[current_section] = {}
            continue

        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        if current_section is None:
            result[key] = value
        else:
            result[current_section][key] = value

    return result

"""Small, quote-aware QE namelist reader; not a complete Fortran validator."""
import re

TOKEN = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|![^\n]*|&[A-Za-z_]+|[A-Za-z_]\w*(?:\s*\([^)]*\))?|[=,/]|[^\s=,/&!]+")


def parse_namelists(text):
    tokens = [m.group() for m in TOKEN.finditer(text) if not m.group().startswith('!')]
    result, active = {}, None
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith('&'):
            active = token[1:].lower()
            if active == 'end':
                active = None
            else:
                result.setdefault(active, {})
        elif token == '/':
            active = None
        elif active and i + 2 < len(tokens) and tokens[i + 1] == '=':
            key = re.sub(r'\s+', '', token.lower())
            value = tokens[i + 2]
            if value[:1] in ("'", '"'):
                quote = value[0]
                value = value[1:-1].replace(quote + quote, quote)
            result[active][key] = value
            i += 2
        i += 1
    return result


def scalar_settings(text):
    return {key: value for values in parse_namelists(text).values() for key, value in values.items()}

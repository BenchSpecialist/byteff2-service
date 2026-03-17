import re
from functools import lru_cache

SUPPORTED_ELEMENT_LABELS = {
    1: 'H',
    6: 'C',
    7: 'N',
    8: 'O',
    9: 'F',
    15: 'P',
    16: 'S',
    17: 'Cl',
    35: 'Br',
    53: 'I',
    3: 'Li',
}

_SUPPORTED_SET = frozenset(SUPPORTED_ELEMENT_LABELS.values())
_AROMATIC_TO_ELEMENT = {'c': 'C', 'n': 'N', 'o': 'O', 's': 'S', 'p': 'P'}
# Two-letter first so "Cl" is not parsed as "C" + "l"; then any other [A-Z][a-z]; then single [A-Z]; then standalone aromatic.
_ELEMENT_PATTERN = re.compile(r'(Cl|Br|Li|[A-Z][a-z]|[A-Z]|(?<![A-Z])[cnops])')


@lru_cache(maxsize=1024)
def _validate_one_smiles(smi: str) -> list[str]:
    """Collect validation errors for a single SMILES string. Returns tuple for cache safety."""
    errors = []
    unsupported_elements = set()
    for m in _ELEMENT_PATTERN.finditer(smi):
        token = m.group(1)
        element = _AROMATIC_TO_ELEMENT.get(token, token)
        if element not in _SUPPORTED_SET:
            unsupported_elements.add(element)

    if unsupported_elements:
        errors = [f"Unsupported element: {element} in '{smi}'" for element in sorted(unsupported_elements)]

    return errors


def validate_smiles(smi_or_smi_list: str | list[str]) -> list[str]:
    """Validate that all element symbols in the given SMILES are supported.

    :param smi_or_smi_list: A single SMILES string or a list of SMILES strings.
    :return: List of error messages; empty if all SMILES are valid.
    """
    smi_list = [smi_or_smi_list] if isinstance(smi_or_smi_list, str) else smi_or_smi_list
    return [err for smi in smi_list for err in _validate_one_smiles(smi)]

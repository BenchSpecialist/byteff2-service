from functools import lru_cache

from rdkit import Chem

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


@lru_cache(maxsize=1024)
def _validate_one_smiles(smi: str) -> list[str]:
    """Collect validation errors for a single SMILES string."""
    unsupported_elements = set()

    mol = Chem.MolFromSmiles(smi)
    if mol is None:
        return [f"RDKit couldn't parse SMILES: {smi}"]

    # union update the unsupported elements set
    unsupported_elements |= {symbol for atom in mol.GetAtoms() if (symbol := atom.GetSymbol()) not in _SUPPORTED_SET}

    if unsupported_elements:
        return [f"Unsupported element: {element} in '{smi}'" for element in sorted(unsupported_elements)]

    return []


def validate_smiles(smi_or_smi_list: str | list[str]) -> list[str]:
    """Validate that all element symbols in the given SMILES are supported.

    :param smi_or_smi_list: A single SMILES string or a list of SMILES strings.
    :return: List of error messages; empty if all SMILES are valid.
    """
    smi_list = [smi_or_smi_list] if isinstance(smi_or_smi_list, str) else smi_or_smi_list
    return [err for smi in smi_list for err in _validate_one_smiles(smi)]

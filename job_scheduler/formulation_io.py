"""
Formulation I/O utilities: CSV/XLSX parsing, validation, and deterministic
UID encoding/decoding for formulations.
"""
import polars
import base64
from typing import Optional

from tools.formulation import (COMMON_NAME_TO_SMILES, SALT_TO_IONS)

# Separators for canonical serialisation (chosen to not clash with chemical names)
_PAIR_SEP = "|"
_KV_SEP = ":"


def get_uniq_id_from_formulation(name_to_fractions: dict[str, float]) -> str:
    """Encode a formulation into a unique, reversible hash string.

    Components are sorted alphabetically by name to ensure order-independence.
    Weight fractions are stored with 4 decimal places.

    :param name_to_fractions: Mapping of component name to weight fraction.
    :return: A deterministic base64url string ID.
    """
    sorted_items = sorted(name_to_fractions.items(), key=lambda x: x[0])
    canonical = _PAIR_SEP.join(f"{name}{_KV_SEP}{fraction:.4f}" for name, fraction in sorted_items)
    return base64.urlsafe_b64encode(canonical.encode("utf-8")).decode("ascii")


def get_formulation_from_uniq_id(uid: str) -> dict[str, float]:
    """Decode a unique hash string back into the original formulation.

    :param uid: The unique ID produced by :func:`get_uniq_id_from_formulation`.
    :return: The original name-to-fraction mapping.
    """
    canonical = base64.urlsafe_b64decode(uid.encode("ascii")).decode("utf-8")
    result: dict[str, float] = {}
    for pair in canonical.split(_PAIR_SEP):
        name, fraction_str = pair.split(_KV_SEP)
        result[name] = float(fraction_str)
    return result


def parse_formulations_file(filepath: str) -> dict[str, list[dict]]:
    """Parse a CSV or XLSX file into formulation groups.

    :param filepath: Path to a ``.csv`` or ``.xlsx`` file.
    :return: Dict mapping ``formulation_id`` to a list of component dicts,
        each with keys ``Component_ID``, ``Name``, ``Weight_fraction``.
    :raises ValueError: On unsupported format or missing columns.
    """
    if filepath.endswith(".csv"):
        df = polars.read_csv(filepath)
    elif filepath.endswith(".xlsx"):
        df = polars.read_excel(filepath, engine="openpyxl")
    else:
        raise ValueError(f"Unsupported file format: {filepath}. Use .csv or .xlsx")

    required_cols = {"Formulation_ID", "Component_ID", "Name", "Weight_fraction"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    formulations: dict[str, list[dict]] = {}
    for row in df.iter_rows(named=True):
        fid = str(row["Formulation_ID"])
        formulations.setdefault(fid, []).append({
            "Component_ID": str(row["Component_ID"]),
            "Name": str(row["Name"]),
            "Weight_fraction": float(row["Weight_fraction"]),
        })
    return formulations


def validate_formulation(components: list[dict]) -> Optional[str]:
    """Validate that weight fractions sum to 100.

    :return: Error message if invalid, ``None`` if valid.
    """
    total = sum(c["Weight_fraction"] for c in components)
    if abs(total - 100.0) > 0.01:
        names = ", ".join(c["Name"] for c in components)
        return (f"Weight fractions sum to {total:.2f}, expected 100. "
                f"Components: {names}")
    return None


def validate_component_names(components: list[dict]) -> list[str]:
    """Validate that all component names can be resolved.

    Solvent and Additive names must exist in :data:`COMMON_NAME_TO_SMILES`.
    Salt names must exist in :data:`SALT_TO_IONS` or :data:`COMMON_NAME_TO_SMILES`.

    :return: List of error messages (empty if all valid).
    """
    errors: list[str] = []
    for comp in components:
        name = comp["Name"]
        role = comp["Component_ID"]
        if role == "Salt":
            if name not in SALT_TO_IONS and name not in COMMON_NAME_TO_SMILES:
                errors.append(f"Unknown salt name: '{name}'. "
                              f"Not found in SALT_TO_IONS or COMMON_NAME_TO_SMILES.")
        else:
            if name not in COMMON_NAME_TO_SMILES:
                errors.append(f"Unknown component name: '{name}'. "
                              f"Not found in COMMON_NAME_TO_SMILES.")
    return errors

"""
Formulation I/O utilities: CSV/XLSX parsing, validation, and deterministic
UID encoding/decoding for formulations.
"""
import polars
import base64
from typing import Optional

from tools.formulation import (
    build_simulation_box_config,
    COMMON_NAME_TO_SMILES,
    _compute_molecule_info
)

# Salt decomposition: compound salt names → (cation, anion) ion names
SALT_TO_IONS: dict[str, tuple[str, str]] = {
    "LiPF6": ("Li", "PF6"),
    "LiBF4": ("Li", "BF4"),
    "LiTFSI": ("Li", "TFSI"),
    "LiFSI": ("Li", "FSI"),
    "LiDFP": ("Li", "DFP"),
}

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
    canonical = _PAIR_SEP.join(
        f"{name}{_KV_SEP}{fraction:.4f}" for name, fraction in sorted_items
    )
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
        raise ValueError(
            f"Unsupported file format: {filepath}. Use .csv or .xlsx"
        )

    required_cols = {"Formulation_ID", "Component_ID", "Name", "Weight_fraction"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    formulations: dict[str, list[dict]] = {}
    for row in df.iter_rows(named=True):
        fid = str(row["Formulation_ID"])
        formulations.setdefault(fid, []).append(
            {
                "Component_ID": str(row["Component_ID"]),
                "Name": str(row["Name"]),
                "Weight_fraction": float(row["Weight_fraction"]),
            }
        )
    return formulations


def validate_formulation(components: list[dict]) -> Optional[str]:
    """Validate that weight fractions sum to 100.

    :return: Error message if invalid, ``None`` if valid.
    """
    total = sum(c["Weight_fraction"] for c in components)
    if abs(total - 100.0) > 0.01:
        names = ", ".join(c["Name"] for c in components)
        return (
            f"Weight fractions sum to {total:.2f}, expected 100. "
            f"Components: {names}"
        )
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
                errors.append(
                    f"Unknown salt name: '{name}'. "
                    f"Not found in SALT_TO_IONS or COMMON_NAME_TO_SMILES."
                )
        else:
            if name not in COMMON_NAME_TO_SMILES:
                errors.append(
                    f"Unknown component name: '{name}'. "
                    f"Not found in COMMON_NAME_TO_SMILES."
                )
    return errors


def build_config_from_weight_fractions(
    name_to_fractions: dict[str, float],
    component_roles: dict[str, str],
    temperature: int = 298,
) -> dict:
    """
    Convert a weight-fraction formulation (name -> weight fraction) into a simulation config.

    Bridges the CSV/XLSX input format (component names + weight fractions
    summing to 100) to the ``build_simulation_box_config()`` interface that
    expects solvent fractions, cation molality, and anion fractions.

    :param name_to_fractions: Component name -> weight fraction (summing to 100).
    :param component_roles: Component name -> role ("Solvent", "Salt", "Additive").
    :param temperature: Simulation temperature in Kelvin.
    :return: Config dict ready for ``TransportProtocol``.
    """
    # Separate components by role
    solvent_names: list[str] = []
    solvent_fracs: list[float] = []
    salt_entries: list[tuple[str, float]] = []  # (salt_name, weight_fraction)

    for name, frac in name_to_fractions.items():
        role = component_roles[name]
        if role in ("Solvent", "Additive"):
            solvent_names.append(name)
            solvent_fracs.append(frac)
        elif role == "Salt":
            salt_entries.append((name, frac))

    # Resolve solvent names to SMILES
    solvent_smiles_list = []
    for name in solvent_names:
        smi = COMMON_NAME_TO_SMILES.get(name, name)
        solvent_smiles_list.append(smi)

    # Normalise solvent weight fractions to sum to 1
    total_solvent_frac = sum(solvent_fracs)
    normalised_solvent_fracs = [f / total_solvent_frac for f in solvent_fracs]

    # Decompose salts into cation + anion and compute molality
    # Collect all unique cation and anion species with their moles
    cation_name = None
    anion_moles: dict[str, float] = {}  # anion_name -> moles per 100g total
    total_cation_moles = 0.0

    for salt_name, salt_wf in salt_entries:
        if salt_name in SALT_TO_IONS:
            cat, ani = SALT_TO_IONS[salt_name]
        else:
            # If salt name is a known ion directly (e.g. individual ion entry)
            cat, ani = None, salt_name

        if cat is not None:
            # Compute molar weight of the compound salt from its ions
            cat_info = _compute_molecule_info(cat)
            ani_info = _compute_molecule_info(ani)
            salt_mw = cat_info["molar_weight"] + ani_info["molar_weight"]
            moles = salt_wf / salt_mw
            total_cation_moles += moles
            anion_moles[ani] = anion_moles.get(ani, 0.0) + moles
            cation_name = cat

    if cation_name is None:
        raise ValueError("No cation found in salt entries")

    # Molality = moles of cation per kg of solvent
    # Solvent mass per 100 g of formulation
    solvent_mass_kg = total_solvent_frac / 1000.0  # weight fracs sum to 100
    cation_molality = total_cation_moles / solvent_mass_kg if solvent_mass_kg > 0 else 1.0

    # Anion fractions relative to total cation moles
    anion_name_list = list(anion_moles.keys())
    anion_frac_list = [
        anion_moles[ani] / total_cation_moles for ani in anion_name_list
    ]

    return build_simulation_box_config({
        "temperature": temperature,
        "solvent_smiles_list": solvent_smiles_list,
        "solvent_fractions": {"weight_fraction_list": normalised_solvent_fracs},
        "cation_name": cation_name,
        "cation_molality": cation_molality,
        "anion_name_list": anion_name_list,
        "anion_fractions": {"mole_fraction_list": anion_frac_list},
    })
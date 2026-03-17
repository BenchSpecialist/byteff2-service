"""
Formulation utilities for converting electrolyte formulation specifications
into MD simulation box configurations.

The main entry point is :func:`build_simulation_box_config`, which accepts a
formulation dict describing solvents, cation, anions, and molality, and
returns a dict containing the integer molecule counts and SMILES strings needed
by the byteff2 simulation protocols.
"""

import warnings
from enum import Enum
from typing import Any, Dict, Optional

from rdkit import Chem
from rdkit.Chem import Descriptors

try:
    from typing import TypedDict
except ImportError:  # Python < 3.8
    from typing_extensions import TypedDict

# Target total atom count used to scale molecule numbers in the simulation box.
TARGET_TOTAL_ATOMS = 10_000


class FractionType(Enum):
    """Specifies whether solvent/anion fractions are given as molar or weight fractions."""

    MOLAR = "mole_fraction_list"
    WEIGHT = "weight_fraction_list"


class MolInfo(TypedDict, total=False):
    """Molecular information for a single component."""

    smiles: str
    canonical_smiles: str
    molar_weight: float  # g/mol, includes implicit H
    num_atoms: int  # heavy + implicit H count
    charge: int  # formal charge
    name: Optional[str]  # common name if known, else auto-generated residue label
    mole_fraction: float  # raw input molar fraction (solvent only)
    weight_fraction: float  # raw input weight fraction (solvent only)
    relative_moles: float  # relative moles used for scaling
    anion_frac: float  # anion molar fraction relative to total cation moles


# Map common names to canonical smiles (RDKit format)
COMMON_NAME_TO_SMILES: dict[str, str] = {
    # Cations
    "Li": "[Li+]",
    # Anions
    "PF6": "F[P-](F)(F)(F)(F)F",  # Hexafluorophosphate
    "BF4": "F[B-](F)(F)F",  # Tetrafluoroborate
    "TFSI": 'O=S(=O)([N-]S(=O)(=O)C(F)(F)F)C(F)(F)F',  # Bis(trifluoromethylsulfonyl)imide
    "FSI": 'O=S(=O)(F)[N-]S(=O)(=O)F',  # Bis(fluorosulfonyl)imide
    "DFP": 'O=P(F)(F)[O-]',  # Difluorophosphate
    # Solvents
    'DMC': 'COC(=O)OC',
    'EC': 'O=C1OCCO1',
    'EMC': 'CCOC(=O)OC',
    'DEC': 'CCOC(=O)OCC',
    'CBS': 'O=C1OC(C2COS(=O)(=O)O2)C(O1)C1COS(=O)(=O)O1',
    "PC": "CC1OCC(=O)O1",  # Propylene carbonate
    "DMSO": "CS(C)=O",  # Dimethyl sulfoxide
    "THF": "C1CCOC1",  # Tetrahydrofuran
    "DME": "COCCOC",  # 1,2-Dimethoxyethane
    "DOL": "CC1OCCO1",  # 1,3-Dioxolane
    "GBL": "O=C1CCCO1",  # γ-Butyrolactone
    "MeCN": "CC#N",  # Acetonitrile
    # Additives (often also solvents)
    "FEC": "O=C1OC(F)CO1",  # Fluoroethylene carbonate
    "VC": "O=C1OC=CO1",  # Vinylene carbonate
}

# Map canonical smiles to common names
COMMON_SMILES_TO_NAME = {v: k for k, v in COMMON_NAME_TO_SMILES.items()}


def _compute_molecule_info(smiles_or_name: str) -> MolInfo:
    """Parse a SMILES string or common name into a :class:`MolInfo` dict.

    Looks up *smiles_or_name* in :data:`COMMON_NAME_TO_SMILES` first; if not
    found it is treated as a literal SMILES string.  The ``name`` field is
    populated from :data:`COMMON_SMILES_TO_NAME` when the canonical SMILES
    matches a known entry.

    :param smiles_or_name: A SMILES string or a key from
        :data:`COMMON_NAME_TO_SMILES` (e.g. ``"Li"``, ``"DMC"``).
    :return: A :class:`MolInfo` dict with ``smiles``, ``canonical_smiles``,
        ``molar_weight``, ``num_atoms``, ``charge``, and ``name`` populated.
    :raises ValueError: If *smiles_or_name* cannot be parsed as a valid SMILES.
    """
    smiles = COMMON_NAME_TO_SMILES.get(smiles_or_name, smiles_or_name)

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid SMILES string: {smiles!r}")

    canonical_smiles = Chem.MolToSmiles(mol, isomericSmiles=False, canonical=True)
    molar_weight = Descriptors.MolWt(mol)  # g/mol, includes implicit hydrogens
    charge = Chem.GetFormalCharge(mol)
    num_atoms = Chem.AddHs(mol).GetNumAtoms()  # heavy + implicit H

    mol_info: MolInfo = {
        "smiles": smiles,
        "canonical_smiles": canonical_smiles,
        "molar_weight": molar_weight,
        "num_atoms": num_atoms,
        "charge": charge,
        "name": COMMON_SMILES_TO_NAME.get(canonical_smiles),
    }

    # try:
    #     import pubchempy as pcp
    # except ImportError:
    #     pcp = None
    # WARNING: using the IUPAC name causes assertion errors in bytemol/core/molecule.py
    # Get IUPAC name of the molecule if applicable
    # elif pcp is not None:
    #     try:
    #         compounds = pcp.get_compounds(canonical_smiles, namespace='smiles')
    #     except Exception as e:
    #         print(f"Error getting IUPAC name for {canonical_smiles}: {e}")
    #         return mol_info
    #     if compounds is not None and len(compounds) > 0:
    #         c = compounds[0]
    #         mol_info["name"] = c.iupac_name

    return mol_info


def build_simulation_box_config(formulation_config: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a formulation specification into a simulation box configuration.

    Accepts a classical electrolyte formulation (molality + molar/weight
    fractions) and returns a dict with integer molecule counts for each
    component, scaled so the total atom count is approximately
    :data:`TARGET_TOTAL_ATOMS`.  Charge neutrality is enforced by adjusting
    anion counts before deriving the cation count.

    Components are ordered in the output as **solvents → cation → anions**,
    and within each group sorted by descending count then alphabetically by
    name.

    :param formulation_config: Formulation dict with the following keys:

        - ``temperature`` *(int, optional)*: Simulation temperature in K.
          Defaults to 298.
        - ``solvent_smiles_list`` *(list[str])*: SMILES strings or common
          names (see :data:`COMMON_NAME_TO_SMILES`) for each solvent.
        - ``solvent_fractions`` *(dict)*: Either
          ``{"mole_fraction_list": [...]}`` or
          ``{"weight_fraction_list": [...]}``.  Fractions are auto-normalised
          if they do not sum to 1.
        - ``cation_name`` *(str)*: SMILES or common name for the cation.
        - ``cation_molality`` *(float)*: Salt concentration in mol kg⁻¹
          (moles of cation per kg of solvent).
        - ``anion_name_list`` *(list[str])*: SMILES or common names for each
          anion species.
        - ``anion_fractions`` *(dict, optional)*: Molar fractions of each
          anion relative to total cation moles.  Defaults to ``[1.0]`` for a
          single anion.

    :return: Dict with keys:

        - ``temperature`` *(int)*
        - ``natoms`` *(int)*: Total atom count (heavy + H) in the box.
        - ``components`` *(dict[str, int])*: Residue name → molecule count.
        - ``smiles`` *(dict[str, str])*: Residue name → input SMILES.

    :raises ValueError: If the cation has zero formal charge, or if any
        SMILES is invalid.
    """
    temperature: int = int(formulation_config.get("temperature") or 298)

    # ------------------------------------------------------------------ #
    # Solvents                                                             #
    # ------------------------------------------------------------------ #
    solvent_smiles_list: list[str] = formulation_config["solvent_smiles_list"]
    raw_solvent_fractions_data: Dict[str, Any] = formulation_config["solvent_fractions"]

    if raw_solvent_fractions_data.get(FractionType.WEIGHT.value) is None:
        solvent_fraction_type = FractionType.MOLAR
        solvent_fractions: list[float] = raw_solvent_fractions_data[FractionType.MOLAR.value]
    else:
        solvent_fraction_type = FractionType.WEIGHT
        solvent_fractions = raw_solvent_fractions_data[FractionType.WEIGHT.value]

    total_frac = sum(solvent_fractions)
    if abs(total_frac - 1.0) > 1e-3:
        warnings.warn(
            f"Solvent fractions sum to {total_frac:.4f} instead of 1.0; auto-normalising.",
            UserWarning,
            stacklevel=2,
        )
        solvent_fractions = [f / total_frac for f in solvent_fractions]

    solvents: list[MolInfo] = []
    solvent_names: list[str] = []
    for i, smi in enumerate(solvent_smiles_list):
        solvent = _compute_molecule_info(smi)
        solvent["mole_fraction"] = solvent_fractions[i]

        if solvent["name"] is None:
            # Residue name in .gro files is limited to 5 characters
            solvent["name"] = f"S{i:02d}"

        solvent_names.append(solvent["name"])
        solvents.append(solvent)

    # Convert input fractions to relative moles
    for solvent in solvents:
        if solvent_fraction_type == FractionType.WEIGHT:
            solvent["relative_moles"] = solvent["mole_fraction"] / solvent["molar_weight"]
        else:
            solvent["relative_moles"] = solvent["mole_fraction"]

    # ------------------------------------------------------------------ #
    # Cation                                                               #
    # ------------------------------------------------------------------ #
    cation_name: str = formulation_config["cation_name"]
    cation_info: MolInfo = _compute_molecule_info(cation_name)
    cation_info["name"] = cation_name

    cation_charge: int = cation_info["charge"]
    if cation_charge == 0:
        raise ValueError(f"Cation {cation_name!r} has formal charge 0; "
                         "cannot build a charge-neutral electrolyte simulation.")

    # ------------------------------------------------------------------ #
    # Anions                                                               #
    # ------------------------------------------------------------------ #
    anion_name_list: list[str] = formulation_config["anion_name_list"]
    raw_anion_fractions: list[float] = (formulation_config.get("anion_fractions", {}).get("mole_fraction_list") or
                                        [1.0])

    anions: list[MolInfo] = []
    for i, name in enumerate(anion_name_list):
        anion = _compute_molecule_info(name)
        anion["name"] = name
        anion["anion_frac"] = raw_anion_fractions[i]
        anions.append(anion)

    # ------------------------------------------------------------------ #
    # Relative moles from molality                                         #
    # ------------------------------------------------------------------ #
    molality: float = formulation_config["cation_molality"]

    # Solvent mass in kg for 1 "unit" of the formulation
    solvent_mass_kg = sum(s["relative_moles"] * s["molar_weight"] for s in solvents) / 1000.0
    total_cation_relative_moles = molality * solvent_mass_kg
    cation_info["relative_moles"] = total_cation_relative_moles

    for anion in anions:
        anion["relative_moles"] = total_cation_relative_moles * anion["anion_frac"]

    # ------------------------------------------------------------------ #
    # Scale to TARGET_TOTAL_ATOMS                                          #
    # ------------------------------------------------------------------ #
    all_components = solvents + [cation_info] + anions
    total_relative_atoms = sum(c["relative_moles"] * c["num_atoms"] for c in all_components)
    scale_factor = TARGET_TOTAL_ATOMS / total_relative_atoms

    output: Dict[str, Any] = {
        "temperature": temperature,
        "natoms": 0,
        "components": {},
        "smiles": {},
    }

    for solvent in solvents:
        count = max(round(solvent["relative_moles"] * scale_factor), 1 if solvent["relative_moles"] > 0 else 0)
        output["components"][solvent["name"]] = count
        output["smiles"][solvent["name"]] = solvent["smiles"]

    # ------------------------------------------------------------------ #
    # Anion counts + charge-neutrality adjustment                          #
    # ------------------------------------------------------------------ #
    anion_counts: Dict[str, int] = {}
    total_anion_charge = 0

    for anion in anions:
        count = max(round(anion["relative_moles"] * scale_factor), 1 if anion["relative_moles"] > 0 else 0)
        anion_counts[anion["name"]] = count
        output["smiles"][anion["name"]] = anion["smiles"]
        total_anion_charge += count * anion["charge"]

    # Bump the first anion one molecule at a time until total anion charge
    # is exactly divisible by the cation charge so we can neutralise cleanly.
    # Guard: skip if the first anion has zero charge (would loop forever).
    first_anion = anions[0]
    if first_anion["charge"] != 0:
        while abs(total_anion_charge) % abs(cation_charge) != 0:
            anion_counts[first_anion["name"]] += 1
            total_anion_charge += first_anion["charge"]

    for anion in anions:
        output["components"][anion["name"]] = anion_counts[anion["name"]]

    # Q_cation + Q_anion = 0  =>  N_cat * q_cat = -Q_anion
    cation_count = -total_anion_charge // cation_charge
    output["components"][cation_info["name"]] = cation_count
    output["smiles"][cation_info["name"]] = cation_info["smiles"]

    # ------------------------------------------------------------------ #
    # Final atom count + charge check                                      #
    # ------------------------------------------------------------------ #
    component_by_name: Dict[str, MolInfo] = {c["name"]: c for c in all_components}

    output["natoms"] = sum(count * component_by_name[name]["num_atoms"]
                           for name, count in output["components"].items()
                           if name in component_by_name)

    net_charge = sum(count * component_by_name[name]["charge"]
                     for name, count in output["components"].items()
                     if name in component_by_name)
    if net_charge != 0:
        warnings.warn(
            f"Simulation box is not charge-neutral! Net charge: {net_charge}",
            RuntimeWarning,
            stacklevel=2,
        )

    # ------------------------------------------------------------------ #
    # Sort: solvents → cation → anions; within each group by descending   #
    # count then alphabetically by name.                                   #
    # ------------------------------------------------------------------ #
    counts = output["components"]
    sort_key = lambda name: (-counts[name], name)

    output["components"] = ({
        name: counts[name] for name in sorted(solvent_names, key=sort_key) if name in counts
    } | {
        cation_name: counts[cation_name]
    } | {
        name: counts[name] for name in sorted(anion_name_list, key=sort_key) if name in counts
    })

    return output


__all__ = ["build_simulation_box_config"]

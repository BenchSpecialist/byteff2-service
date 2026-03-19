"""
Formulation utilities for converting electrolyte formulation specifications
into MD simulation box configurations.
"""

import warnings
from enum import Enum
from functools import lru_cache
from typing import Any, Dict, List, Optional

from rdkit import Chem
from rdkit.Chem import Descriptors

try:
    from typing import TypedDict
except ImportError:  # Python < 3.8
    from typing_extensions import TypedDict

# Target total atom count used to scale molecule numbers in the simulation box.
TARGET_TOTAL_ATOMS = 10_000
MIN_TOTAL_ATOMS = 8_000
MAX_TOTAL_ATOMS = 15_000
PREFER_SALTS_MIN = 40
ALLOW_SALTS_MIN = 30


def _linspace(start: float, stop: float, num: int) -> list[float]:
    """Return evenly spaced floats including both endpoints."""
    if num <= 1:
        return [float(start)]
    step = (stop - start) / float(num - 1)
    return [start + i * step for i in range(num)]


SCALE_MULTIPLIERS = sorted({round(v, 6) for v in (_linspace(0.70, 1.30, 61) + _linspace(0.90, 1.10, 81))})


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


# Map common names to CANONICAL smiles (RDKit format)
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
    'EC': 'O=C1OCCO1',  # Ethylene carbonate
    'EMC': 'CCOC(=O)OC',  # Ethyl methyl carbonate
    'DEC': 'CCOC(=O)OCC',  # Diethyl carbonate
    "CBS": "O=C1OC(C2COS(=O)(=O)O2)C(C2COS(=O)(=O)O2)O1",
    "PC": "CC1OCC(=O)O1",  # Propylene carbonate
    "DMSO": "CS(C)=O",  # Dimethyl sulfoxide
    "THF": "C1CCOC1",  # Tetrahydrofuran
    "DME": "COCCOC",  # 1,2-Dimethoxyethane
    "DOL": "CC1OCCO1",  # 1,3-Dioxolane
    "GBL": "O=C1CCCO1",  # γ-Butyrolactone
    "MeCN": "CC#N",  # Acetonitrile
    "EP": "CCOC(=O)CC",  # Ethyl Propionate
    # Additives (often also solvents)
    "VC": "O=c1occo1",  # Vinylene carbonate
    "FEC": "O=C1OCC(F)O1",  # Fluoroethylene carbonate
    "DFP": "O=P([O-])(F)F",  # difluorophosphate anion
    "PS": "O=S1(=O)CCCO1",  # 1,3-Propane sultone
    "DTD": "O=S1(=O)OCCO1",  # 1,3,2-Dioxathiolane 2,2-dioxide
    "DMSF": "COS(=O)OC",  # Dimethyl sulfite
}

# Map canonical smiles to common names
COMMON_SMILES_TO_NAME = {v: k for k, v in COMMON_NAME_TO_SMILES.items()}

# Salt decomposition: compound salt names → (cation, anion) ion names
SALT_TO_IONS: dict[str, tuple[str, str]] = {
    "LiPF6": ("Li", "PF6"),
    "LiBF4": ("Li", "BF4"),
    "LiTFSI": ("Li", "TFSI"),
    "LiFSI": ("Li", "FSI"),
    "LiDFP": ("Li", "DFP"),
}


@lru_cache(maxsize=256)
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

    Accepts a classical electrolyte formulation (cation molality + molar/weight
    fractions of solvents and anions) and returns a dict with integer molecule
    counts for each component, scaled so the total atom count is approximately
    :data:`TARGET_TOTAL_ATOMS`. Charge neutrality is enforced by adjusting
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
        solvent = dict(_compute_molecule_info(smi))
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
    cation_info: MolInfo = dict(_compute_molecule_info(cation_name))
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
        anion = dict(_compute_molecule_info(name))
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

    return _build_box_from_relative_moles(solvents, solvent_names, cation_info, cation_name, anions, anion_name_list,
                                          temperature)


def _build_box_from_relative_moles(
    solvents: List[MolInfo],
    solvent_names: List[str],
    cation_info: MolInfo,
    cation_name: str,
    anions: List[MolInfo],
    anion_name_list: List[str],
    temperature: int,
) -> Dict[str, Any]:
    """Scale relative moles to ~TARGET_TOTAL_ATOMS and apply charge neutrality.

    All components must have ``relative_moles`` set. Returns the same structure
    as :func:`build_simulation_box_config` (temperature, natoms, components, smiles).

    :param solvents: Solvent MolInfo list with relative_moles.
    :param solvent_names: Order of solvent names for output.
    :param cation_info: Cation MolInfo with relative_moles.
    :param cation_name: Cation name key.
    :param anions: Anion MolInfo list with relative_moles.
    :param anion_name_list: Order of anion names for output.
    :param temperature: Simulation temperature in K.
    :return: Config dict with components (int counts), smiles, natoms, temperature.
    """
    cation_charge: int = cation_info["charge"]
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

    anion_counts: Dict[str, int] = {}
    total_anion_charge = 0
    for anion in anions:
        count = max(round(anion["relative_moles"] * scale_factor), 1 if anion["relative_moles"] > 0 else 0)
        anion_counts[anion["name"]] = count
        output["smiles"][anion["name"]] = anion["smiles"]
        total_anion_charge += count * anion["charge"]

    first_anion = anions[0]
    if first_anion["charge"] != 0:
        while abs(total_anion_charge) % abs(cation_charge) != 0:
            anion_counts[first_anion["name"]] += 1
            total_anion_charge += first_anion["charge"]

    for anion in anions:
        output["components"][anion["name"]] = anion_counts[anion["name"]]

    cation_count = -total_anion_charge // cation_charge
    output["components"][cation_info["name"]] = cation_count
    output["smiles"][cation_info["name"]] = cation_info["smiles"]

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


def build_config_from_weight_fractions(name_to_weight_fractions: Dict[str, float],
                                       component_roles: Dict[str, str],
                                       temperature: int = 298,
                                       use_legacy_on_no_solution: bool = True) -> Dict[str, Any]:
    """Build a simulation box from component weight fractions using constrained search.

    The input fractions may sum to 1 or 100 and are normalised automatically.
    Each component is converted to relative moles from weight and molecular
    weight. Salt components are expanded into ions with :data:`SALT_TO_IONS`,
    where salt moles are ``w_salt / (MW_cation + MW_anion)``.

    The function then searches a grid of scale factors and chooses the best
    integer molecule counts by minimising:

    - hard constraints (priority order):
      1) salt count shortfall below ``ALLOW_SALTS_MIN``
      2) atom-count violation outside ``[MIN_TOTAL_ATOMS, MAX_TOTAL_ATOMS]``
      3) soft salt shortfall below ``PREFER_SALTS_MIN``
    - soft score: weighted weight-fraction error + atom-count deviation from
      :data:`TARGET_TOTAL_ATOMS`

    Charge neutrality is enforced per candidate by adjusting the dominant
    counter-ion count as needed.

    :param name_to_weight_fractions: Component name -> weight fraction.
        Fractions are expected as 0..1 or 0..100 and are normalised internally.
    :param component_roles: Component name -> role string, typically one of
        ``"Solvent"``, ``"Additive"``, ``"Salt"``, ``"Cation"``, ``"Anion"``.
        Unknown/missing roles default to ``"Solvent"`` behavior.
    :param temperature: Simulation temperature in K.
    :param use_legacy_on_no_solution: If True, fall back to legacy scaling
        logic when constrained search cannot find a valid candidate.
    :return: Dict with:
        - ``temperature`` (int)
        - ``natoms`` (int)
        - ``components`` (dict[str, int]): molecule counts by component name
        - ``smiles`` (dict[str, str]): component SMILES by component name
    :raises ValueError: If no active components exist, no ionic species are
        present, or no valid candidate can be found (and fallback is disabled).
    """
    total_frac = sum(name_to_weight_fractions.values())
    if abs(total_frac - 1.0) > 1e-3:
        if abs(total_frac - 100.0) < 1e-3:
            name_to_weight_fractions = {k: v / 100.0 for k, v in name_to_weight_fractions.items()}
        else:
            name_to_weight_fractions = {k: v / total_frac for k, v in name_to_weight_fractions.items()}

    # Build species-level targets and relative moles.
    # Salt entries (e.g. LiPF6) are expanded into cation + anion species.
    species_info: Dict[str, MolInfo] = {}
    species_relative_moles: Dict[str, float] = {}
    species_target_wf: Dict[str, float] = {}
    species_is_salt: Dict[str, bool] = {}

    def _add_species(name: str, wf: float, is_salt: bool, *, rel_moles: Optional[float] = None) -> None:
        info = dict(_compute_molecule_info(COMMON_NAME_TO_SMILES.get(name, name)))
        species_name = (info.get("name") or name) if not is_salt else name
        info["name"] = species_name
        species_info.setdefault(species_name, info)
        species_target_wf[species_name] = species_target_wf.get(species_name, 0.0) + wf
        species_is_salt[species_name] = species_is_salt.get(species_name, False) or is_salt
        if rel_moles is None:
            rel_moles = wf / float(info["molar_weight"])
        species_relative_moles[species_name] = species_relative_moles.get(species_name, 0.0) + rel_moles

    for comp_name, wf in name_to_weight_fractions.items():
        if wf <= 0:
            continue
        role = (component_roles.get(comp_name) or "Solvent").strip().lower()
        is_salt_role = role == "salt" or (role in ("solvent", "additive") and comp_name in SALT_TO_IONS)

        if is_salt_role:
            if comp_name not in SALT_TO_IONS:
                raise ValueError(f"Unknown salt name: {comp_name!r}. Add to :data:`SALT_TO_IONS`.")
            cation, anion = SALT_TO_IONS[comp_name]
            cation_info = _compute_molecule_info(COMMON_NAME_TO_SMILES.get(cation, cation))
            anion_info = _compute_molecule_info(COMMON_NAME_TO_SMILES.get(anion, anion))
            salt_mw = float(cation_info["molar_weight"]) + float(anion_info["molar_weight"])
            moles = wf / salt_mw
            _add_species(cation, wf * float(cation_info["molar_weight"]) / salt_mw, True, rel_moles=moles)
            _add_species(anion, wf * float(anion_info["molar_weight"]) / salt_mw, True, rel_moles=moles)
            continue

        _add_species(comp_name, wf, role in ("cation", "anion"))

    active = [n for n, m in species_relative_moles.items() if m > 0]
    if not active:
        raise ValueError("No active components found from weight fractions.")

    cation_names = [n for n in active if int(species_info[n]["charge"]) > 0]
    anion_names = [n for n in active if int(species_info[n]["charge"]) < 0]
    if not cation_names or not anion_names:
        raise ValueError("Both cation and anion species are required to build an electrolyte box.")

    atom_per_scale = sum(species_relative_moles[n] * int(species_info[n]["num_atoms"]) for n in active)
    if atom_per_scale <= 0:
        raise ValueError("Invalid component set: atom-per-scale is non-positive.")

    salt_per_scale = sum(species_relative_moles[n] for n in cation_names if species_is_salt.get(n, False))
    target_atoms = TARGET_TOTAL_ATOMS
    scale0 = target_atoms / atom_per_scale

    candidate_scales: set[float] = set()
    for m in SCALE_MULTIPLIERS:
        s = scale0 * float(m)
        if s > 0:
            candidate_scales.add(round(s, 8))

    if salt_per_scale > 0:
        for center in (ALLOW_SALTS_MIN / salt_per_scale, PREFER_SALTS_MIN / salt_per_scale):
            for m in _linspace(0.80, 1.25, 46):
                s = center * float(m)
                if s > 0:
                    candidate_scales.add(round(s, 8))

    for center in (MIN_TOTAL_ATOMS / atom_per_scale, MAX_TOTAL_ATOMS / atom_per_scale):
        for m in _linspace(0.90, 1.10, 21):
            s = center * float(m)
            if s > 0:
                candidate_scales.add(round(s, 8))

    best: Optional[Dict[str, Any]] = None
    primary_cation = max(cation_names, key=lambda n: species_relative_moles[n])
    primary_anion = max(anion_names, key=lambda n: species_relative_moles[n])

    def _evaluate(scale: float) -> Optional[Dict[str, Any]]:
        counts = {n: max(int(round(scale * species_relative_moles[n])), 1) for n in active}
        net_charge = sum(counts[n] * int(species_info[n]["charge"]) for n in active)

        if net_charge > 0:
            anion_charge = int(species_info[primary_anion]["charge"])
            if anion_charge >= 0:
                return None
            while net_charge > 0:
                counts[primary_anion] += 1
                net_charge += anion_charge
        elif net_charge < 0:
            cation_charge = int(species_info[primary_cation]["charge"])
            if cation_charge <= 0:
                return None
            while net_charge < 0:
                counts[primary_cation] += 1
                net_charge += cation_charge

        total_atoms = int(sum(counts[n] * int(species_info[n]["num_atoms"]) for n in active))
        total_salts = int(sum(counts[n] for n in cation_names if species_is_salt.get(n, False)))

        masses = {n: counts[n] * float(species_info[n]["molar_weight"]) for n in active}
        total_mass = float(sum(masses.values()))
        if total_mass <= 0:
            return None

        wt_ach = {n: masses[n] / total_mass for n in active}
        abs_errs = [abs(wt_ach[n] - species_target_wf.get(n, 0.0)) * 100.0 for n in active]
        max_abs_err = max(abs_errs) if abs_errs else 0.0
        mean_abs_err = sum(abs_errs) / len(abs_errs) if abs_errs else 0.0

        hard_salt = max(0, ALLOW_SALTS_MIN - total_salts) if salt_per_scale > 0 else 0
        hard_atoms = max(0, MIN_TOTAL_ATOMS - total_atoms) + max(0, total_atoms - MAX_TOTAL_ATOMS)
        soft_salt = max(0, PREFER_SALTS_MIN - total_salts) if salt_per_scale > 0 else 0
        hard = (int(hard_salt), int(hard_atoms), int(soft_salt))

        atom_dev = abs(total_atoms - target_atoms) / max(1.0, float(target_atoms))
        soft = 2.0 * max_abs_err + 1.0 * mean_abs_err + 1.2 * atom_dev

        return {
            "hard": hard,
            "soft": soft,
            "counts": counts,
            "total_atoms": total_atoms,
        }

    for scale in sorted(candidate_scales):
        cand = _evaluate(scale)
        if cand is None:
            continue
        if best is None or (cand["hard"], cand["soft"]) < (best["hard"], best["soft"]):
            best = cand

    if best is None:
        if use_legacy_on_no_solution:
            warnings.warn(
                "Constrained search found no valid candidate; falling back to legacy builder.",
                RuntimeWarning,
                stacklevel=2,
            )
            return build_config_from_weight_fractions_no_opt(
                name_to_weight_fractions=name_to_weight_fractions,
                component_roles=component_roles,
                temperature=temperature,
            )
        raise ValueError("Failed to build a valid configuration from weight fractions.")

    counts = best["counts"]
    smiles = {n: str(species_info[n]["smiles"]) for n in active}

    solvent_out = [n for n in active if int(species_info[n]["charge"]) == 0]
    cation_out = [n for n in active if int(species_info[n]["charge"]) > 0]
    anion_out = [n for n in active if int(species_info[n]["charge"]) < 0]
    sort_key = lambda n: (-counts[n], n)
    components = ({
        n: counts[n] for n in sorted(solvent_out, key=sort_key)
    } | {
        n: counts[n] for n in sorted(cation_out, key=sort_key)
    } | {
        n: counts[n] for n in sorted(anion_out, key=sort_key)
    })

    return {
        "temperature": int(temperature),
        "natoms": int(best["total_atoms"]),
        "components": components,
        "smiles": smiles,
    }


def build_config_from_weight_fractions_no_opt(
    name_to_weight_fractions: Dict[str, float],
    component_roles: Dict[str, str],
    temperature: int = 298,
) -> Dict[str, Any]:
    """Legacy conversion path kept as fallback for hard edge cases."""
    total_frac = sum(name_to_weight_fractions.values())
    if abs(total_frac - 1.0) > 1e-3:
        if abs(total_frac - 100.0) < 1e-3:
            name_to_weight_fractions = {k: v / 100.0 for k, v in name_to_weight_fractions.items()}
        else:
            name_to_weight_fractions = {k: v / total_frac for k, v in name_to_weight_fractions.items()}

    solvent_names: List[str] = []
    solvents: List[MolInfo] = []
    cation_name: Optional[str] = None
    cation_info: Optional[MolInfo] = None
    anion_moles: Dict[str, float] = {}
    anion_name_list: List[str] = []

    for name, wf in name_to_weight_fractions.items():
        role = component_roles.get(name, "Solvent")
        is_salt = role == "Salt" or (role in ("Solvent", "Additive") and "Li" in name)
        if role == "Cation":
            info = dict(_compute_molecule_info(COMMON_NAME_TO_SMILES.get(name, name)))
            if info["charge"] <= 0:
                raise ValueError(f"Cation {name!r} has non-positive charge.")
            if cation_info is None:
                cation_name = name
                cation_info = info
                cation_info["name"] = name
                cation_info["relative_moles"] = wf / info["molar_weight"]
            else:
                cation_info["relative_moles"] = cation_info.get("relative_moles", 0.0) + wf / info["molar_weight"]
            continue
        if role == "Anion":
            info = dict(_compute_molecule_info(COMMON_NAME_TO_SMILES.get(name, name)))
            anion_moles[name] = anion_moles.get(name, 0.0) + wf / info["molar_weight"]
            if name not in anion_name_list:
                anion_name_list.append(name)
            continue
        if not is_salt:
            info = dict(_compute_molecule_info(COMMON_NAME_TO_SMILES.get(name, name)))
            info["name"] = info.get("name") or f"S{len(solvent_names):02d}"
            info["relative_moles"] = wf / info["molar_weight"]
            solvent_names.append(info["name"])
            solvents.append(info)
        else:
            if name not in SALT_TO_IONS:
                raise ValueError(f"Unknown salt name: {name!r}. Add to :data:`SALT_TO_IONS`.")
            cat, ani = SALT_TO_IONS[name]
            cat_info = _compute_molecule_info(COMMON_NAME_TO_SMILES.get(cat, cat))
            ani_info = _compute_molecule_info(COMMON_NAME_TO_SMILES.get(ani, ani))
            salt_mw = cat_info["molar_weight"] + ani_info["molar_weight"]
            moles = wf / salt_mw
            if cation_info is None:
                cation_name = cat
                cation_info = dict(cat_info)
                cation_info["name"] = cat
            anion_moles[ani] = anion_moles.get(ani, 0.0) + moles
            if ani not in anion_name_list:
                anion_name_list.append(ani)

    if cation_info is None or cation_name is None:
        raise ValueError("No salt (cation) found in formulation.")

    if "relative_moles" not in cation_info:
        cation_info["relative_moles"] = sum(anion_moles.values())
    anions: List[MolInfo] = []
    for ani in anion_name_list:
        ainfo = dict(_compute_molecule_info(COMMON_NAME_TO_SMILES.get(ani, ani)))
        ainfo["name"] = ani
        ainfo["relative_moles"] = anion_moles[ani]
        anions.append(ainfo)

    return _build_box_from_relative_moles(solvents, solvent_names, cation_info, cation_name, anions, anion_name_list,
                                          temperature)


def get_weight_fractions_from_molecule_counts(smi_to_count: Dict[str, int]) -> Dict[str, float]:
    """Get weight fractions from molecule counts.

    :param smi_to_count: SMILES to molecule count dictionary.
    :return: Weight fraction dictionary.
    """
    weights = [_compute_molecule_info(smi)["molar_weight"] * count for smi, count in smi_to_count.items()]
    total_weight = sum(weights)
    return {smi: weight / total_weight for smi, weight in zip(smi_to_count.keys(), weights)}


__all__ = ["build_simulation_box_config", "build_config_from_weight_fractions"]

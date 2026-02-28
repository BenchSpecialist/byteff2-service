import json
from enum import Enum
from typing import Dict, Any
from rdkit import Chem
from rdkit.Chem import Descriptors

try:
    import pubchempy as pcp
except ImportError:
    pcp = None

# Default target total atoms in the simulation box
TARGET_TOTAL_ATOMS = 10000


class SolventType(Enum):
    MOLAR_FRACTION = "mole_fraction_list"
    WEIGHT_FRACTION = "weight_fraction_list"


class AnionType(Enum):
    MOLAR_FRACTION = "mole_fraction_list"
    WEIGHT_FRACTION = "weight_fraction_list"


# Map common names to canonical smiles (RDKit format)
COMMON_NAME_TO_SMILES = {
    "Li": "[Li+]",
    "PF6": "F[P-](F)(F)(F)(F)F",  # Hexafluorophosphate
    "BF4": "F[B-](F)(F)F",  # Tetrafluoroborate
    "TFSI": 'O=S(=O)([N-]S(=O)(=O)C(F)(F)F)C(F)(F)F',  # Bis(trifluoromethylsulfonyl)imide
    "FSI": 'O=S(=O)(F)[N-]S(=O)(=O)F',  # Bis(fluorosulfonyl)imide
    # common solvents
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
}

# Map canonical smiles to common names
COMMON_SMILES_TO_NAME = {v: k for k, v in COMMON_NAME_TO_SMILES.items()}


def get_mol_info(smiles_or_name: str):
    smiles = COMMON_NAME_TO_SMILES.get(smiles_or_name, smiles_or_name)

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid SMILES string: {smiles}")

    canonical_smiles = Chem.MolToSmiles(mol, isomericSmiles=False, canonical=True)
    molar_weight = Descriptors.MolWt(mol)  # includes implicit hydrogens
    charge = Chem.GetFormalCharge(mol)

    mol_with_h = Chem.AddHs(mol)
    num_atoms = mol_with_h.GetNumAtoms()

    mol_info = {
        "smiles": smiles,
        "canonical_smiles": canonical_smiles,
        "molar_weight": molar_weight,
        "num_atoms": num_atoms,
        "charge": charge,
        "name": None,
    }

    # Check if the molecule is already in the common name to smiles dictionary
    if canonical_smiles in COMMON_SMILES_TO_NAME:
        mol_info["name"] = COMMON_SMILES_TO_NAME[canonical_smiles]

    # Note: use IUPAC name will cause name assertion error in bytemol/core/molecule.py
    # comment out for now
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


def convert_md_config(data: Dict[str, Any]) -> Dict[str, Any]:
    temperature = data.get("temperature")
    if not temperature:
        temperature = 298

    solvents = []
    solvent_names = []
    solvent_smiles = data["solvent_smiles_list"]
    s_fractions = data["solvent_fractions"]

    if s_fractions.get("weight_fraction_list") is None:
        solvent_type = SolventType.MOLAR_FRACTION
        solvent_fractions = s_fractions["mole_fraction_list"]
    else:
        solvent_type = SolventType.WEIGHT_FRACTION
        solvent_fractions = s_fractions["weight_fraction_list"]

    total_frac = sum(solvent_fractions)
    if abs(total_frac - 1.0) > 1e-3:
        print("WARNING: Invalid total fraction")
        solvent_fractions = [f / total_frac for f in solvent_fractions]

    for i, smi in enumerate(solvent_smiles):
        info = get_mol_info(smi)
        info["raw_fraction"] = solvent_fractions[i]

        if info["name"] is None:
            # Used as residue name in .gro file, which is limited to 5 characters
            info["name"] = f"S{str(i).zfill(2)}"

        solvent_names.append(info["name"])
        solvents.append(info)

    if solvent_type == SolventType.WEIGHT_FRACTION:
        for s in solvents:
            s["rel_moles"] = s["raw_fraction"] / s["molar_weight"]
    else:
        for s in solvents:
            s["rel_moles"] = s["raw_fraction"]

    cation_name = data["cation_name"]
    cation_info = get_mol_info(cation_name)
    cation_info["name"] = cation_name

    anion_names = data["anion_name_list"]
    a_fractions_data = data["anion_fractions"]
    anion_fractions = a_fractions_data.get("mole_fraction_list")
    if not anion_fractions:
        anion_fractions = [1.0]

    anions = []
    for i, name in enumerate(anion_names):
        info = get_mol_info(name)
        info["name"] = name
        info["rel_frac"] = anion_fractions[i]
        anions.append(info)

    molality = data["cation_molality"]

    total_solvent_rel_mass_g = sum(s["rel_moles"] * s["molar_weight"] for s in solvents)
    total_solvent_rel_mass_kg = total_solvent_rel_mass_g / 1000.0

    total_cation_rel_moles = molality * total_solvent_rel_mass_kg
    cation_info["rel_moles"] = total_cation_rel_moles

    for a in anions:
        a["rel_moles"] = total_cation_rel_moles * a["rel_frac"]

    total_rel_atoms = 0
    all_components_temp = solvents + [cation_info] + anions
    for comp in all_components_temp:
        total_rel_atoms += comp["rel_moles"] * comp["num_atoms"]

    scale_factor = TARGET_TOTAL_ATOMS / total_rel_atoms

    output = {"temperature": int(temperature), "natoms": 0, "components": {}, "smiles": {}}

    for s in solvents:
        count = int(round(s["rel_moles"] * scale_factor))
        if count == 0 and s["rel_moles"] > 0:
            count = 1
        output["components"][s["name"]] = count
        output["smiles"][s["name"]] = s["smiles"]

    current_anion_charge = 0
    anion_counts_map = {}

    for a in anions:
        count = int(round(a["rel_moles"] * scale_factor))
        if count == 0 and a["rel_moles"] > 0:
            count = 1

        anion_counts_map[a["name"]] = count
        output["smiles"][a["name"]] = a["smiles"]

        current_anion_charge += count * a["charge"]

    cat_charge = cation_info["charge"]
    if cat_charge == 0:
        raise ValueError("Cation charge cannot be 0 for electrolyte simulation.")

    while abs(current_anion_charge) % abs(cat_charge) != 0:
        first_anion = anions[0]
        anion_counts_map[first_anion["name"]] += 1
        current_anion_charge += first_anion["charge"]

    for a in anions:
        output["components"][a["name"]] = anion_counts_map[a["name"]]

    # Q_cation + Q_anion = 0  =>  N_cat * q_cat = -Q_anion
    target_cation_charge = -current_anion_charge
    cation_count = target_cation_charge // cat_charge

    output["components"][cation_info["name"]] = cation_count
    output["smiles"][cation_info["name"]] = cation_info["smiles"]

    final_atom_count = 0

    comp_lookup = {c["name"]: c for c in solvents + anions + [cation_info]}

    for name, count in output["components"].items():
        comp_obj = comp_lookup.get(name)
        if comp_obj:
            final_atom_count += count * comp_obj["num_atoms"]

    output["natoms"] = final_atom_count

    total_charge_check = 0
    for name, count in output["components"].items():
        comp_obj = comp_lookup.get(name)
        if comp_obj:
            total_charge_check += count * comp_obj["charge"]

    if total_charge_check != 0:
        print(f"CRITICAL WARNING: System is not neutral! Total charge: {total_charge_check}")

    # Order by Solvents-Cations-Anions;
    # Order by value
    # Order by name
    component_outputs = output["components"]

    component_sorted = {k: v for k, v in sorted(component_outputs.items(), key=lambda x: (x[1], x[0]), reverse=True)}

    solvent_output = {k: v for k, v in component_sorted.items() if k in solvent_names}

    cation_output = {k: v for k, v in component_sorted.items() if k in [cation_name]}

    anion_output = {k: v for k, v in component_sorted.items() if k in anion_names}

    output["components"] = {}
    for k, v in solvent_output.items():
        output["components"][k] = v
    for k, v in cation_output.items():
        output["components"][k] = v
    for k, v in anion_output.items():
        output["components"][k] = v

    return output

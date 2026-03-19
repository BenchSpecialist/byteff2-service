from tools.formulation import (build_simulation_box_config, build_config_from_weight_fractions,
                               build_config_from_weight_fractions_no_opt, get_weight_fractions_from_molecule_counts)


def test_build_simulation_box_config():
    formulation_spec = {
        "solvent_smiles_list": ["COC(=O)OC", "C1COC(=O)O1"],
        "solvent_fractions": {
            "weight_fraction_list": [0.6, 0.4]
        },
        "anion_name_list": ["PF6"],
        "anion_fractions": {
            "mole_fraction_list": [1.0]
        },
        "cation_name": "Li",
        "num_cations": 35,
        "cation_molality": 1.0,
        "simulation_box_size": 500.0,
        "temperature": 298.15
    }
    config = build_simulation_box_config(formulation_spec)

    assert config["natoms"] == 9998
    assert config["components"] == {"DMC": 499, "EC": 341, "Li": 75, "PF6": 75}
    assert config["smiles"] == {"DMC": "COC(=O)OC", "EC": "C1COC(=O)O1", "PF6": "F[P-](F)(F)(F)(F)F", "Li": "[Li+]"}


def _format_data(data: list[tuple[str, str, float]]) -> tuple[dict[str, float], dict[str, str]]:
    name_to_fractions = {name: fraction / 100 for _, name, fraction in data}
    component_roles = {name: role for role, name, _ in data}
    return name_to_fractions, component_roles


def test_build_config_from_weight_fractions():
    test_data = [
        ("Solvent", "EC", 10),
        ("Solvent", "EMC", 75),
        ("Salt", "LiPF6", 12),
        ("Additive", "VC", 0.5),
        ("Additive", "FEC", 2),
        ("Additive", "LiDFP", 0.5),
    ]
    config = build_config_from_weight_fractions_no_opt(*_format_data(test_data))
    assert config["natoms"] == 10005
    assert config["components"] == {'EMC': 561, 'EC': 88, 'FEC': 15, 'VC': 5, 'Li': 66, 'PF6': 62, 'DFP': 4}
    assert config["smiles"] == {
        'EC': 'O=C1OCCO1',
        'EMC': 'CCOC(=O)OC',
        'VC': 'O=c1occo1',
        'FEC': 'O=C1OCC(F)O1',
        'PF6': 'F[P-](F)(F)(F)(F)F',
        'DFP': 'O=P([O-])(F)F',
        'Li': '[Li+]'
    }

    config1 = build_config_from_weight_fractions(*_format_data(test_data), use_legacy_on_no_solution=False)
    assert config1["natoms"] == 10105
    assert config1["components"] == {'EMC': 567, 'EC': 89, 'FEC': 15, 'VC': 5, 'Li': 66, 'PF6': 62, 'DFP': 4}
    assert config1["smiles"] == {
        'EC': 'O=C1OCCO1',
        'EMC': 'CCOC(=O)OC',
        'Li': '[Li+]',
        'PF6': 'F[P-](F)(F)(F)(F)F',
        'VC': 'O=c1occo1',
        'FEC': 'O=C1OCC(F)O1',
        'DFP': 'O=P([O-])(F)F'
    }

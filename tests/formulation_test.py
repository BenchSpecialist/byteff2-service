from byteff2.toolkit.formulation import build_simulation_box_config


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

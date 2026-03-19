from tools.validate import validate_smiles


def test_validate_smiles():
    # Test valid SMILES
    assert validate_smiles(["C1=CC=CC=C1", "Cc1ccccc1"]) == []

    # Test invalid SMILES
    assert validate_smiles("B(O[Si](C)(C)C)(O[Si](C)(C)C)O[Si](C)(C)C") == [
        "Unsupported element: B in 'B(O[Si](C)(C)C)(O[Si](C)(C)C)O[Si](C)(C)C'",
        "Unsupported element: Si in 'B(O[Si](C)(C)C)(O[Si](C)(C)C)O[Si](C)(C)C'",
    ]

    # Test list of SMILES
    smi_list = ["c1ccccc1", "C[Si](C)(C)OP(=O)(O)O"]
    assert validate_smiles(smi_list) == [
        "Unsupported element: Si in 'C[Si](C)(C)OP(=O)(O)O'",
    ]

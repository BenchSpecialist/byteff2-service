import torch
from functools import wraps
from typing import Dict, List, Any, Callable

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

_DEFAULT_CUTOFF = 6.  # angstrom


def track_gpu_memory(func: Callable) -> Callable:

    @wraps(func)
    def wrapper(*args, **kwargs):
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            initial_memory = torch.cuda.memory_allocated() / 1024**2  # MB
            max_memory_before = torch.cuda.max_memory_allocated() / 1024**2

            print(f"[{func.__name__}] Initial GPU memory: {initial_memory:.2f} MB")

            result = func(*args, **kwargs)

            peak_memory = torch.cuda.max_memory_allocated() / 1024**2  # MB
            final_memory = torch.cuda.memory_allocated() / 1024**2
            memory_used = peak_memory - max_memory_before

            print(f"[{func.__name__}] Peak GPU memory: {peak_memory:.2f} MB")
            print(f"[{func.__name__}] Final GPU memory: {final_memory:.2f} MB")
            print(f"[{func.__name__}] Memory used by function: {memory_used:.2f} MB")

            # Reset peak memory tracking
            torch.cuda.reset_peak_memory_stats()
        else:
            result = func(*args, **kwargs)

        return result

    return wrapper


def get_cluster_category(cluster_size: int) -> str:
    """
    Map cluster size to solvation category.

    :param cluster_size: Number of anion COMs within cutoff distance of a cation COM
    :return: Category string: "SSIP", "CIP", or "AGG"
    """
    if cluster_size == 0:
        return "SSIP"
    elif cluster_size == 1:
        return "CIP"
    else:  # cluster_size >= 2
        return "AGG"


@track_gpu_memory
def calc_solvation_cluster_distribution(species_mass_dict: Dict[str, float],
                                        species_number_dict: Dict[str, int],
                                        anion: List[str],
                                        cation: List[str],
                                        md_volume: float,
                                        nvt_positions: torch.Tensor,
                                        batch_size: int = 1000) -> Dict[str, Any]:
    """
    Compute solvation cluster distribution based on the number of anion COMs within a cutoff
    distance from each cation COM.

    For each frame and for each cation, the number of anions whose center-of-mass
    lies within a cutoff distance (first-minimum detection from RDF) is counted,
    and a normalized frequency distribution is computed:
        P(k) = Prob( a cation has exactly k anions within cutoff )
    where k is the number of anion COMs within the cutoff distance of a given
    cation COM.

    Mathematically:
        1. Molecular center-of-mass (COM):
            R_i(t) = ( Σ_a m_{i,a} r_{i,a}(t) ) / ( Σ_a m_{i,a} )

        2. Minimum image displacement:
            ΔR_ij = R_i^- - R_j^+
                     - L * round((R_i^- - R_j^+) / L)

        3. Discrete coordination number per cation:
            k_j(t) = Σ_i Θ( r_cut^2 - |ΔR_ij(t)|^2 )

        4. Distribution:
            P(k) = (# occurrences of k across all frames and cations)
                   / (N_frames x N_cations)

    The output distribution can be directly mapped to solvation categories:
        k = 0  → SSIP (solvent-separated ion pair)
        k = 1  → CIP  (contact ion pair)
        k ≥ 2  → AGG  (ion aggregate)

    This function defines ion pairing using molecular center-of-mass (COM) distances,
    not atom–atom distances. This implies:
        - An anion is considered "coordinated" if its COM lies within cutoff distance
          from a cation COM.
        - Direct atom-level coordination (e.g., Li-O contact) is not explicitly checked.
    Consequences:
        • For small, compact anions (e.g., FSI-, PF6-), COM distance often
            correlates well with physical contact.
        • For large or asymmetric anions (e.g., TFSI-), COM may lie outside
            cutoff even when coordinating atoms are in contact.
        • This method measures molecular proximity, not explicit chemical
            bonding or denticity.

    ------------------------------------------------------------------------
    :param species_mass_dict:
        Dictionary mapping species name → list of atomic masses for that species.
        Used to compute molecular center-of-mass positions via mass-weighting.
        Example: {"Li": [6.94], "FSI": [32.06, 19.00, 16.00, ...]}

    :param species_number_dict:
        Dictionary mapping species name → number of molecules in simulation.
        Example: {"Li": 100, "FSI": 100}

    :param anion: List of species names treated as anions.

    :param cation: List of species names treated as cations.

    :param md_volume: Simulation box volume (assumed cubic). Box length is computed as L = md_volume^(1/3)

    :param nvt_positions: Atomic coordinates with shape (n_frames, n_atoms, 3)
                          Positions must correspond exactly to the atom ordering
                          implied byspecies_mass_dict and species_number_dict.

    :param batch_size: Number of frames to process in each batch

    :return:
        Dictionary mapping:
            "cluster": List of dictionaries, each containing:
                "size": int, number of anion COMs within cutoff distance of a cation COM
                "category": str, solvation category: "SSIP", "CIP", or "AGG"
                "fraction": float, fraction over all frames and all cations
            "rdf_cn_data": Optional, list of lists, each containing:
                [r, g, cn], where:
                    r: float, distance in Å
                    g: float, RDF value
                    cn: float, coordination number
            "_cutoff": float, the first minimum of g(r) to be used as cutoff in solvation
                       cluster identification, in angstrom
    """

    box_size = md_volume**(1 / 3)
    box_tensor = torch.tensor([box_size] * 3, dtype=torch.float, device=DEVICE)
    box_tensor = box_tensor.view(1, 1, 1, 3)

    mol_index = []
    weight_index = []

    n_mols = 0
    n_anions = 0
    n_cations = 0

    collection_names = anion + cation

    for name in collection_names:
        num_atoms = len(species_mass_dict[name])
        total_mass = sum(species_mass_dict[name])
        weight_ratio = [species_mass_dict[name][i] / total_mass for i in range(num_atoms)]
        for _ in range(species_number_dict[name]):
            mol_index.extend([n_mols] * num_atoms)
            weight_index.extend(weight_ratio)
            n_mols += 1
            if name in cation:
                n_cations += 1
            elif name in anion:
                n_anions += 1

    n_frames = len(nvt_positions)

    weight_index = torch.tensor(weight_index, dtype=torch.float, device=DEVICE)
    nvt_positions_torch = torch.tensor(nvt_positions[:, :len(mol_index), :], dtype=torch.float, device=DEVICE)

    weighted_positions = nvt_positions_torch * weight_index.unsqueeze(0).unsqueeze(-1)
    mol_index = torch.tensor(mol_index, dtype=torch.long,
                             device=DEVICE).unsqueeze(0).unsqueeze(-1).expand_as(weighted_positions)

    # Get molecular COM for every frame, sum atoms per molecule: R_i(t) = sum_a w_{i,a} * r_{i,a}(t)
    com_tensor = torch.scatter_add(input=torch.zeros(size=(n_frames, n_mols, 3), device=DEVICE),
                                   dim=1,
                                   index=mol_index,
                                   src=weighted_positions)
    anion_com_tensor = com_tensor[:, :n_anions, :]
    cation_com_tensor = com_tensor[:, n_anions:, :]

    # Clear intermediate tensors to free up memory
    del com_tensor, weighted_positions, mol_index, nvt_positions_torch
    torch.cuda.empty_cache()

    rdf_array, r_cut = None, _DEFAULT_CUTOFF
    print(f"Cutoff for first solvation shell: {r_cut} Å")

    # Process frames in batches: (batch_size, n_anions, n_cations, 3)
    # instead of (n_frames, n_anions, n_cations, 3) to avoid OOM
    coordination_counts_list = []
    for batch_start in range(0, n_frames, batch_size):
        batch_end = min(batch_start + batch_size, n_frames)

        # Get batch of COM tensors: (batch_frames, n_anions/cations, 3)
        anion_com_batch = anion_com_tensor[batch_start:batch_end]
        cation_com_batch = cation_com_tensor[batch_start:batch_end]

        # Pairwise distance between COM of anions and cations
        # for one batch: (batch_frames, n_anions, n_cations, 3)
        shift = anion_com_batch.unsqueeze(2) - cation_com_batch.unsqueeze(1)
        # apply periodic boundary conditions
        shift = shift - box_tensor * torch.round(shift / box_tensor)

        # Compute the distance squared: (batch_frames, n_anions, n_cations)
        com_tensor_distance = torch.sum(shift**2, dim=-1)

        # Get discrete coordination number, use dynamic cutoff for coordination counting
        # Θ(r_ij(t) < r_cut^2) = 1 if r_ij(t) < r_cut, 0 otherwise
        # for one batch: (batch_frames, n_cations)
        coordination_counts_batch = torch.sum(com_tensor_distance < r_cut**2, dim=1)
        coordination_counts_list.append(coordination_counts_batch)

        # Clear batch tensors
        del shift, com_tensor_distance, coordination_counts_batch, anion_com_batch, cation_com_batch
        torch.cuda.empty_cache()

    # Concatenate all batches: (n_frames, n_cations)
    coordination_counts = torch.cat(coordination_counts_list, dim=0)
    counts, freqs = torch.unique(coordination_counts, return_counts=True)
    freqs = freqs / n_frames / n_cations

    cluster_distribution = [{
        "size": k,
        "category": get_cluster_category(int(k)),
        "fraction": v,
    } for k, v in zip(counts.tolist(), freqs.tolist())]

    result = {"cluster": cluster_distribution, "_cutoff": r_cut}
    if rdf_array is not None:
        result["rdf_cn_data"] = rdf_array
    return result

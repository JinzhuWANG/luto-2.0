import numpy as np

import luto.settings as settings


def get_cost_env_plantings(data) -> np.ndarray:
    """
    Parameters
    ----------
    data: object/module
        Data object or module with fields like in `luto.data`.

    Returns
    -------
    np.ndarray
        Cost of environmental plantings for each cell. 1-D array Indexed by cell.
    """
    ep_maintenance_cost = settings.ENV_PLANTING_COST_PER_HA_PER_YEAR
    # yearly maintenance cost of EP applied to each cell and adjusted for resfactor
    return ep_maintenance_cost * data.REAL_AREA


def get_cost_matrix(data):
    """
    Returns non-agricultural c_rk matrix of costs per cell and land use.
    """
    env_plantings_costs = get_cost_env_plantings(data)

    # reshape each non-agricultural matrix to be indexed (r, k) and concatenate on the k indexing
    non_agr_c_matrices = [
        env_plantings_costs.reshape((data.NCELLS, 1)),
    ]

    return np.concatenate(non_agr_c_matrices, axis=1)
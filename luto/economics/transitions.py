#!/bin/env python3
#
# transitions.py - data about transition costs.
#
# Author: Fjalar de Haan (f.dehaan@deakin.edu.au)
# Created: 2021-04-30
# Last modified: 2021-10-11
#

import os.path

import numpy as np
import pandas as pd
import numpy_financial as npf

from luto.economics.quantity import lvs_veg_types, get_yield_pot
from luto.economics.cost import get_cost_matrices

def amortise(cost, rate=0.05, horizon=30):
    """Return amortised `cost` at `rate`interest over `horizon` years."""
    return -1 * npf.pmt(rate, horizon, pv=cost, fv=0, when='begin')

def get_exclude_matrices(data, lumap):
    """Return x_mrj exclude matrices."""
    # To be excluded based on SA2 data.
    x_sa2 = data.EXCLUDE

    # Raw transition-cost matrix is in AUD/Ha and lexigraphically ordered.
    t_ij = data.TMATRIX

    # Infer number of cells from lumap array.
    ncells = lumap.shape[0]

    # Transition costs to commodity j at cell r using present lumap (in AUD/ha).
    t_rj = np.stack(tuple(t_ij[lumap[r]] for r in range(ncells)))

    # To be excluded based on disallowed switches.
    x_trn = np.where(np.isnan(t_rj), 0, 1)

    # Overal exclusion as elementwise, logical `and` of the exclude matrices.
    return x_sa2 * x_trn

def get_transition_matrices(data, year, lumap, lmmap):
    """Return t_mrj transition-cost matrices.

    A transition-cost matrix gives the cost of switching a certain cell r to
    a certain land-use j under a certain land-management m. The base costs are
    taken from the raw transition costs in the `data` module and additional
    costs are added depending on the land-management (e.g. costs of irrigation
    infrastructure). Switching costs further depend on both the current and the
    future land-use, so the present land-use map is needed.

    Parameters
    ----------

    data: object/module
        Data object or module with fields like in `luto.data`.
    year : int
        Number of years from base year, counting from zero.
    lumap : numpy.ndarray
        Present land-use map, i.e. highpos (shape=ncells, dtype=int).
    lmmap : numpy.ndarray
        Present land-management map (shape=ncells, dtype=int).

    Returns
    -------

    numpy.ndarray
        t_mrj transition-cost matrices. The m-slices correspond to the
        different land-management versions of the land-use `j` to switch _to_.
        With m==0 conventional dry-land, m==1 conventional irrigated.
    """

    # Raw transition-cost matrix is in AUD/Ha and lexigraphically ordered.
    t_ij = data.TMATRIX

    # Infer number land-uses and cells from t_ij and lumap matrices.
    nlus = t_ij.shape[0]
    ncells = lumap.shape[0]

    # The cost matrices are needed as well.
    c_mrj = get_cost_matrices(data, year)

    # Transition costs to commodity j at cell r using present lumap (in AUD/ha).
    t_rj = np.stack(tuple(t_ij[lumap[r]] for r in range(ncells)))

    # Convert water requirements for LVSTK from per head to per hectare.
    AQ_REQ_LVSTK_DRY_RJ = data.AQ_REQ_LVSTK_DRY_RJ.copy()
    AQ_REQ_LVSTK_IRR_RJ = data.AQ_REQ_LVSTK_IRR_RJ.copy()
    for lu in data.LANDUSES:
        if lu in data.LU_LVSTK:
            lvs, veg = lvs_veg_types(lu)
            j = data.LANDUSES.index(lu)
            AQ_REQ_LVSTK_DRY_RJ[:, j] *= get_yield_pot(data, lvs, veg, 'dry')
            AQ_REQ_LVSTK_IRR_RJ[:, j] *= get_yield_pot(data, lvs, veg, 'irr')

    # Foregone income is incurred @ 3x production cost unless ...
    odelta_todry_rj = 3 * c_mrj[0]
    odelta_toirr_rj = 3 * c_mrj[1]

    # ... the switch is to an unallocated land use.
    odelta_todry_rj.T[data.LU_UNALL_INDICES] = 0
    odelta_toirr_rj.T[data.LU_UNALL_INDICES] = 0

    # Switching may incur water licence cost/refund and infrastructure costs.
    wdelta_toirr_rj = np.zeros((ncells, nlus))
    wdelta_todry_rj = np.zeros((ncells, nlus))

    for r in range(ncells):
        j = lumap[r] # Current land-use index.
        m = lmmap[r] # Current land-man index.

        # DRY -> {DRY, IRR} (i.e. cases _from_ dry land uses.)
        if m == 0:
            # Just switching dry -> irr does not incur the foregone income cost.
            odelta_toirr_rj[r, j] = 0

            # -------------------------------------------------------------- #
            # DRY -> DRY / Licence difference costs.                         #
            # -------------------------------------------------------------- #

            # Net water requirements.
            aq_req_net = ( data.AQ_REQ_CROPS_DRY_RJ[r]
                         +      AQ_REQ_LVSTK_DRY_RJ[r]
                         - data.AQ_REQ_CROPS_DRY_RJ[r, j]
                         -      AQ_REQ_LVSTK_DRY_RJ[r, j] )
            # To pay: net water requirements x licence price.
            wdelta_todry_rj[r] = aq_req_net * data.WATER_LICENCE_PRICE[r]

            # -------------------------------------------------------------- #
            # DRY -> IRR / Licence diff. + infrastructure cost @10kAUD/ha.   #
            # -------------------------------------------------------------- #

            # Net water requirements.
            aq_req_net = ( data.AQ_REQ_CROPS_IRR_RJ[r]
                         +      AQ_REQ_LVSTK_IRR_RJ[r]
                         - data.AQ_REQ_CROPS_DRY_RJ[r, j]
                         -      AQ_REQ_LVSTK_DRY_RJ[r, j] )
            # To pay: net water requirements x licence price and 10kAUD.
            wdelta_toirr_rj[r] = aq_req_net * data.WATER_LICENCE_PRICE[r] + 1E4

        # IRR -> {DRY, IRR} (i.e. cases _from_ irrigated land uses.)
        elif m == 1:
            # Just switching irr -> dry does not incur the foregone income cost.
            odelta_todry_rj[r, j] = 0

            # -------------------------------------------------------------- #
            # IRR -> DRY / Licence diff. plus additional costs at @3kAUD/ha. #
            # ---------------------------------------------------------------#

            # Net water requirements.
            aq_req_net = ( data.AQ_REQ_CROPS_DRY_RJ[r]
                         +      AQ_REQ_LVSTK_DRY_RJ[r]
                         - data.AQ_REQ_CROPS_IRR_RJ[r, j]
                         -      AQ_REQ_LVSTK_IRR_RJ[r, j] )
            # To pay: net water requirements x licence price and 3000.
            wdelta_todry_rj[r] = aq_req_net * data.WATER_LICENCE_PRICE[r] + 3000

            # -------------------------------------------------------------- #
            # IRR -> IRR / Difference with current licence paid or refunded. #
            # -------------------------------------------------------------- #

            # Net water requirements.
            aq_req_net = ( data.AQ_REQ_CROPS_IRR_RJ[r]
                         +      AQ_REQ_LVSTK_IRR_RJ[r]
                         - data.AQ_REQ_CROPS_IRR_RJ[r, j]
                         -      AQ_REQ_LVSTK_IRR_RJ[r, j] )
            # To pay: net water requirements x licence price.
            wdelta_toirr_rj[r] = aq_req_net * data.WATER_LICENCE_PRICE[r]
            # Extra costs for irr infra change @10kAUD/ha if not lvstk -> lvstk.
            infradelta_j = 1E4 * np.ones(nlus)
            infradelta_j[j] = 0 # No extra cost if no land-use change at all.
            if j in data.LU_LVSTK_INDICES: # No extra cost within lvstk lus.
                infradelta_j[data.LU_LVSTK_INDICES] = 0
            wdelta_toirr_rj[r] += infradelta_j # Add cost to total to pay.

        # ??? -> ___ / This case does not (yet) exist.
        else:
            raise ValueError("Unknown land management: %s." % m)

    # Add the various deltas to the base costs and convert to AUD/cell.
    t_rj_todry = ( t_rj                            # Base switching costs.
                 + wdelta_todry_rj                 # Water-related costs.
                 + odelta_todry_rj                 # Foregone income costs.
                 ) * data.REAL_AREA[:, np.newaxis] # Conversion to AUD/cell.
    t_rj_toirr = ( t_rj                            # Base switching costs.
                 + wdelta_toirr_rj                 # Water-related costs.
                 + odelta_toirr_rj                 # Foregone income costs.
                 ) * data.REAL_AREA[:, np.newaxis] # Conversion to AUD/cell.

    # Transition costs are amortised.
    t_rj_todry = amortise(t_rj_todry)
    t_rj_toirr = amortise(t_rj_toirr)

    # Stack the t_rj matrices into one t_mrj array.
    t_mrj = np.stack((t_rj_todry, t_rj_toirr))

    return t_mrj

# Copyright 2025 Bryan, B.A., Williams, N., Archibald, C.L., de Haan, F., Wang, J.,
# van Schoten, N., Hadjikakou, M., Sanson, J.,  Zyngier, R., Marcos-Martinez, R.,
# Navarro, J.,  Gao, L., Aghighi, H., Armstrong, T., Bohl, H., Jaffe, P., Khan, M.S.,
# Moallemi, E.A., Nazari, A., Pan, X., Steyl, D., and Thiruvady, D.R.
#
# This file is part of LUTO2 - Version 2 of the Australian Land-Use Trade-Offs model
#
# LUTO2 is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# LUTO2 is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# LUTO2. If not, see <https://www.gnu.org/licenses/>.


"""
Provides minimalist Solver class and pure helper functions.
"""

import numpy as np
import gurobipy as gp
import luto.settings as settings

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, Any
from gurobipy import GRB

from luto import tools
from luto.solvers.input_data import SolverInputData
from luto.settings import (
    AG_MANAGEMENTS, 
    AG_MANAGEMENTS_REVERSIBLE, 
    NON_AG_LAND_USES, 
    NON_AG_LAND_USES_REVERSIBLE
)


# Set Gurobi environment.
gurenv = gp.Env(logfilename="gurobi.log", empty=True)  # (empty = True)
gurenv.setParam("Method", settings.SOLVE_METHOD)
gurenv.setParam("OutputFlag", settings.VERBOSE)
gurenv.setParam("Presolve", settings.PRESOLVE)
gurenv.setParam("Aggregate", settings.AGGREGATE)
gurenv.setParam("OptimalityTol", settings.OPTIMALITY_TOLERANCE)
gurenv.setParam("FeasibilityTol", settings.FEASIBILITY_TOLERANCE)
gurenv.setParam("BarConvTol", settings.BARRIER_CONVERGENCE_TOLERANCE)
gurenv.setParam("ScaleFlag", settings.SCALE_FLAG)
gurenv.setParam("NumericFocus", settings.NUMERIC_FOCUS)
gurenv.setParam("Threads", settings.THREADS)
gurenv.setParam("BarHomogeneous", settings.BARHOMOGENOUS)
gurenv.setParam("Crossover", settings.CROSSOVER)
gurenv.start()


@dataclass
class SolverSolution:
    lumap: np.ndarray
    lmmap: np.ndarray
    ammaps: dict[str, np.ndarray]
    ag_X_mrj: np.ndarray
    non_ag_X_rk: np.ndarray
    ag_man_X_mrj: dict[str, np.ndarray]
    prod_data: dict[str, Any]
    obj_val: dict[str, float]


class LutoSolver:
    """
    Class responsible for grouping the Gurobi model, relevant input data, and its variables.
    """

    def __init__(
        self,
        input_data: SolverInputData,
    ):

        self._input_data = input_data
        self.gurobi_model = gp.Model(f"LUTO {settings.VERSION}", env=gurenv)

        # Initialise variable stores
        self.X_ag_dry_vars_jr = None
        self.X_ag_irr_vars_jr = None
        self.X_non_ag_vars_kr = None
        self.X_ag_man_dry_vars_jr = None
        self.X_ag_man_irr_vars_jr = None
        self.V = None
        self.E = None
        self.W = None

        # Initialise constraint lookups
        self.cell_usage_constraint_r = {}
        self.ag_management_constraints_r = defaultdict(list)
        self.adoption_limit_constraints = []
        self.demand_penalty_constraints = []
        self.water_limit_constraints = []
        self.water_nyiled_exprs = {}
        self.ghg_expr = None
        self.ghg_consts_ub = None
        self.ghg_consts_lb = None
        self.ghg_consts_soft = []
        self.bio_GBF2_expr = None
        self.bio_GBF2_constrs = {}
        self.bio_GBF3_exprs = {}
        self.bio_GBF3_constrs = {}
        self.bio_GBF4_SNES_exprs = {}
        self.bio_GBF4_SNES_constrs = {}
        self.bio_GBF4_ECNES_exprs = {}
        self.bio_GBF4_ECNES_constrs = {}
        self.bio_GBF8_exprs = {}
        self.bio_GBF8_constrs = {}
        self.regional_adoption_constrs = []


    def formulate(self):
        """
        Performs the initial formulation of the model - setting up decision variables,
        constraints, and the objective.
        """
        print("Setting up the model...")
        self._setup_vars()
        self._setup_constraints()
        self._setup_objective()


    def _setup_vars(self):
        print("...Setting up decision variables...")
        self._setup_ag_vars()
        self._setup_non_ag_vars()
        self._setup_ag_management_variables()
        self._setup_deviation_penalties()

    def _setup_constraints(self):
        print("...Adding the constraints...")
        self._add_cell_usage_constraints()
        self._add_agricultural_management_constraints()
        self._add_agricultural_management_adoption_limit_constraints()
        self._add_demand_penalty_constraints()
        self._add_ghg_emissions_limit_constraints()
        self._add_biodiversity_constraints()
        self._add_regional_adoption_constraints()
        self._add_water_usage_limit_constraints() 
        
    def _setup_objective(self):
        """
        Formulate the objective based on settings.OBJECTIVE
        """
        print(f"...Setting up the objective function to {settings.OBJECTIVE}...")

        # Get objectives 
        self.obj_economy = self._setup_economy_objective()    
        self.obj_biodiv = self._setup_biodiversity_objective()   
        self.obj_penalties = self._setup_penalty_objectives()                                                                    
 
        # Set the objective function
        if settings.OBJECTIVE == "mincost":
            sense = GRB.MINIMIZE
            obj_wrap = (
                self.obj_economy  * settings.SOLVE_WEIGHT_ALPHA 
                - self.obj_biodiv * (1 - settings.SOLVE_WEIGHT_ALPHA)
            )
            objective = (
                obj_wrap * (1 - settings.SOLVE_WEIGHT_BETA) + 
                self.obj_penalties * settings.SOLVE_WEIGHT_BETA
            )
        elif settings.OBJECTIVE == "maxprofit":
            sense = GRB.MAXIMIZE
            obj_wrap = (
                self.obj_economy  * settings.SOLVE_WEIGHT_ALPHA 
                + self.obj_biodiv * (1 - settings.SOLVE_WEIGHT_ALPHA)
            )
            objective = (
                obj_wrap * (1 - settings.SOLVE_WEIGHT_BETA) 
                - self.obj_penalties * settings.SOLVE_WEIGHT_BETA
            )
        else:
            raise ValueError(f"    Unknown objective function: {settings.OBJECTIVE}")

        self.gurobi_model.setObjective(objective, sense)
           

    def _setup_ag_vars(self):
        print("    |__ setting up decision variables for agricultural land uses...")
        self.X_ag_dry_vars_jr = np.zeros(
            (self._input_data.n_ag_lus, self._input_data.ncells), dtype=object
        )
        self.X_ag_irr_vars_jr = np.zeros(
            (self._input_data.n_ag_lus, self._input_data.ncells), dtype=object
        )
        for j in range(self._input_data.n_ag_lus):
            dry_lu_cells = self._input_data.ag_lu2cells[0, j]
            for r in dry_lu_cells:
                self.X_ag_dry_vars_jr[j, r] = self.gurobi_model.addVar(
                    ub=1, name=f"X_ag_dry_{j}_{r}".replace(" ", "_")
                )

            irr_lu_cells = self._input_data.ag_lu2cells[1, j]
            for r in irr_lu_cells:
                self.X_ag_irr_vars_jr[j, r] = self.gurobi_model.addVar(
                    ub=1, name=f"X_ag_irr_{j}_{r}".replace(" ", "_")
                )

    def _setup_non_ag_vars(self):
        print("    |__ setting up decision variables for non-agricultural land uses...")
        self.X_non_ag_vars_kr = np.zeros(
            (self._input_data.n_non_ag_lus, self._input_data.ncells), dtype=object
        )
        
        for k, k_name in enumerate(NON_AG_LAND_USES):
            if not NON_AG_LAND_USES[k_name]:
                continue
            lu_cells = self._input_data.non_ag_lu2cells[k]
            for r in lu_cells:
                x_lb = (
                    0
                    if NON_AG_LAND_USES_REVERSIBLE[k_name]
                    else self._input_data.non_ag_lb_rk[r, k]
                )
                self.X_non_ag_vars_kr[k, r] = self.gurobi_model.addVar(
                    lb=x_lb,
                    ub=self._input_data.non_ag_x_rk[r, k],
                    name=f"X_non_ag_{k}_{r}".replace(" ", "_")
                )

    def _setup_ag_management_variables(self):
        print("    |__ setting up decision variables for agricultural management options...")
        self.X_ag_man_dry_vars_jr = {
            am: np.zeros((len(am_j_list), self._input_data.ncells), dtype=object)
            for am, am_j_list in self._input_data.am2j.items()
        }
        self.X_ag_man_irr_vars_jr = {
            am: np.zeros((len(am_j_list), self._input_data.ncells), dtype=object)
            for am, am_j_list in self._input_data.am2j.items()
        }

        for am, am_j_list in self._input_data.am2j.items():
            if not AG_MANAGEMENTS[am]:
                continue

            # Get snake_case version of the AM name for the variable name
            am_name = tools.am_name_snake_case(am)

            for j_idx, j in enumerate(am_j_list):
                # Create variable for all eligible cells - all lower bounds are zero
                dry_lu_cells = self._input_data.ag_lu2cells[0, j]
                irr_lu_cells = self._input_data.ag_lu2cells[1, j]

                # for savanna burning, remove extra ineligible cells
                if am_name == "savanna_burning":
                    dry_lu_cells = np.intersect1d(
                        dry_lu_cells, self._input_data.savanna_eligible_r
                    )

                for r in dry_lu_cells:
                    dry_x_lb = (
                        0
                        if AG_MANAGEMENTS_REVERSIBLE[am]
                        else self._input_data.ag_man_lb_mrj[am][0, r, j]
                    )
                    self.X_ag_man_dry_vars_jr[am][j_idx, r] = self.gurobi_model.addVar(
                        lb=dry_x_lb,
                        ub=1,
                        name=f"X_ag_man_dry_{am_name}_{j}_{r}".replace(" ", "_"),
                    )
                
                for r in irr_lu_cells:
                    irr_x_lb = (
                        0
                        if AG_MANAGEMENTS_REVERSIBLE[am]
                        else self._input_data.ag_man_lb_mrj[am][1, r, j]
                    )
                    self.X_ag_man_irr_vars_jr[am][j_idx, r] = self.gurobi_model.addVar(
                        lb=irr_x_lb,
                        ub=1,
                        name=f"X_ag_man_irr_{am_name}_{j}_{r}".replace(" ", "_"),
                    )

    def _setup_deviation_penalties(self):
        """
        Decision variables, V, E and W, and B for soft constraints.
        1) [V] Penalty vector for demand, each one corespondes a commodity, that minimises the deviations from demand.
        2) [E] A single penalty scalar for GHG emissions, minimises its deviation from the target.
        3) [W] Penalty vector for water usage, each one corespondes a region, that minimises the deviations from the target.
        4) [B] A single penalty scalar for biodiversity, minimises its deviation from the target.
        """
        print("    |__ Setting up decision variables for soft constraints...")
        
        self.V = self.gurobi_model.addMVar(self._input_data.ncms, lb=0, name="V") # force lb=0 to make sure demand penalties are positive; i.e., demand must be met or exceeded

        if settings.GHG_CONSTRAINT_TYPE == "soft":
            self.E = self.gurobi_model.addVar(name="E")

        if settings.WATER_CONSTRAINT_TYPE == "soft":
            num_regions = len(self._input_data.limits["water"].keys())
            self.W = self.gurobi_model.addMVar(num_regions, name="W")

        
    def _setup_economy_objective(self):
        print("    |__ setting up objective for economy...")
        
        # Get economic contributions
        ag_obj_mrj, non_ag_obj_rk, ag_man_objs = self._input_data.economic_contr_mrj

        ag_exprs = []
        for j in range(self._input_data.n_ag_lus):
            ag_exprs.append(
                ag_obj_mrj[0, self._input_data.ag_lu2cells[0, j], j]
                @ self.X_ag_dry_vars_jr[j, self._input_data.ag_lu2cells[0, j]]
                + ag_obj_mrj[1, self._input_data.ag_lu2cells[1, j], j]
                @ self.X_ag_irr_vars_jr[j, self._input_data.ag_lu2cells[1, j]]
            )

        ag_mam_exprs = []
        for am, am_j_list in self._input_data.am2j.items():
            if not AG_MANAGEMENTS[am]:
                continue
            for j_idx, j in enumerate(am_j_list):
                ag_mam_exprs.append(
                    ag_man_objs[am][0, self._input_data.ag_lu2cells[0, j], j_idx]
                    @ self.X_ag_man_dry_vars_jr[am][j_idx, self._input_data.ag_lu2cells[0, j]]
                    + ag_man_objs[am][1, self._input_data.ag_lu2cells[1, j], j_idx]
                    @ self.X_ag_man_irr_vars_jr[am][j_idx, self._input_data.ag_lu2cells[1, j]]
                )

        non_ag_exprs = []
        for k, k_name in enumerate(NON_AG_LAND_USES):
            if not NON_AG_LAND_USES[k_name]:
                continue
            non_ag_exprs.append(
                non_ag_obj_rk[:, k][self._input_data.non_ag_lu2cells[k]]
                @ self.X_non_ag_vars_kr[k, self._input_data.non_ag_lu2cells[k]]
            )
        
        self.economy_ag_contr = gp.quicksum(ag_exprs)
        self.economy_ag_man_contr = gp.quicksum(ag_mam_exprs)
        self.economy_non_ag_contr = gp.quicksum(non_ag_exprs)
        
        return (
            (self.economy_ag_contr + self.economy_ag_man_contr + self.economy_non_ag_contr) 
            * self._input_data.scale_factors['Economy'] 
            / 1e6 # Convert to million AUD
        )  
    
    
    def _setup_biodiversity_objective(self):
        print("    |__ setting up objective for biodiversity...")
        
        ag_exprs = []
        for j in range(self._input_data.n_ag_lus):
            ag_exprs.append(
                gp.quicksum(
                    self._input_data.ag_b_mrj[0, :, j] * self.X_ag_dry_vars_jr[j, :]
                )  
                + gp.quicksum(
                    self._input_data.ag_b_mrj[1, :, j] * self.X_ag_irr_vars_jr[j, :]
                )
                
            )
 
        ag_mam_exprs = []
        for am, am_j_list in self._input_data.am2j.items():
            if not AG_MANAGEMENTS[am]:
                continue
                
            for j_idx in range(len(am_j_list)):
                ag_mam_exprs.append(
                    gp.quicksum(
                        self._input_data.ag_man_b_mrj[am][0, :, j_idx]
                        * self.X_ag_man_dry_vars_jr[am][j_idx, :]
                    )  # Dryland alt. ag. management contributions
                    + gp.quicksum(
                        self._input_data.ag_man_b_mrj[am][1, :, j_idx]
                        * self.X_ag_man_irr_vars_jr[am][j_idx, :]
                    )  # Irrigated alt. ag. management contributions   
                )
    
        non_ag_exprs = []
        for k,k_name in enumerate(NON_AG_LAND_USES):
            if not NON_AG_LAND_USES[k_name]:
                continue
            non_ag_exprs.append(
                gp.quicksum(
                    self._input_data.non_ag_b_rk[:, k] * self.X_non_ag_vars_kr[k, :]
                )
            )
        
        self.bio_ag_contr = gp.quicksum(ag_exprs)
        self.bio_ag_man_contr = gp.quicksum(ag_mam_exprs)
        self.bio_non_ag_contr = gp.quicksum(non_ag_exprs)
        
        return (
            (self.bio_ag_contr + self.bio_non_ag_contr + self.bio_ag_man_contr) 
            * self._input_data.scale_factors['Biodiversity']
        )
        
        
    def _setup_penalty_objectives(self):
        print("    |__ setting up objective for soft constraints...")

        penalty_ghg = 0
        penalty_water = 0
        
        weight_ghg = 0
        weight_water = 0

        # Get the penalty values for each sector
        penalty_demand = (
            gp.quicksum(
                self.V[c] * self._input_data.scale_factors['Demand'] * price
                for c, price in enumerate(self._input_data.economic_BASE_YR_prices)
            ) 
            * settings.SOLVER_WEIGHT_DEMAND
            / 1e6  # Convert to million AUD
        )
    
        if settings.GHG_CONSTRAINT_TYPE == "soft":
            weight_ghg = settings.SOLVER_WEIGHT_GHG
            penalty_ghg = (
                self.E 
                 * self._input_data.scale_factors['GHG']
                 * weight_ghg
                 / self._input_data.base_yr_prod["BASE_YR GHG (tCO2e)"]
                 + 1
            ) 
        
        if settings.WATER_CONSTRAINT_TYPE == "soft":
            weight_water = settings.SOLVER_WEIGHT_WATER
            penalty_water = (
                gp.quicksum(v for v in self.W)
                 * self._input_data.scale_factors['Water']
                 * weight_water
                 / self._input_data.base_yr_prod["BASE_YR Water (ML)"].sum()
                 + 1
            ) / len(self._input_data.limits["water"].keys()) 

        return (penalty_demand + penalty_ghg + penalty_water) / (settings.SOLVER_WEIGHT_DEMAND + weight_ghg + weight_water)
        

        

    def _add_cell_usage_constraints(self, cells: Optional[np.array] = None):
        """
        Constraint that all of every cell is used for some land use.
        If `cells` is provided, only adds constraints for the given cells
        """
        print("    |__ Adding constraints for cell usage...")

        if cells is None:
            cells = np.array(range(self._input_data.ncells))

        x_ag_dry_vars = self.X_ag_dry_vars_jr[:, cells]
        x_ag_irr_vars = self.X_ag_irr_vars_jr[:, cells]
        x_non_ag_vars = self.X_non_ag_vars_kr[:, cells]

        # Create an array indexed by cell that contains the sums of each cell's variables.
        # Then, loop through the array and add the constraint that each expression must equal 1.
        X_sum_r = (
            x_ag_dry_vars.sum(axis=0)
            + x_ag_irr_vars.sum(axis=0)
            + x_non_ag_vars.sum(axis=0)
        )
        for r, expr in zip(cells, X_sum_r):
            self.cell_usage_constraint_r[r] = self.gurobi_model.addConstr(
                expr == 1, 
                name=f"const_cell_usage_{r}"
            )

    def _add_agricultural_management_constraints(
        self, cells: Optional[np.array] = None
    ):
        """
        Constraint handling alternative agricultural management options:
        Ag. man. variables cannot exceed the value of the agricultural variable.
        """
        print("    |__ Adding constraints for agricultural management options...")

        for am, am_j_list in self._input_data.am2j.items():
            for j_idx, j in enumerate(am_j_list):
                if cells is not None:
                    lm_dry_r_vals = [
                        r for r in cells if self._input_data.ag_x_mrj[0, r, j]
                    ]
                    lm_irr_r_vals = [
                        r for r in cells if self._input_data.ag_x_mrj[1, r, j]
                    ]
                else:
                    lm_dry_r_vals = self._input_data.ag_lu2cells[0, j]
                    lm_irr_r_vals = self._input_data.ag_lu2cells[1, j]

                for r in lm_dry_r_vals:
                    constr = self.gurobi_model.addConstr(
                        self.X_ag_man_dry_vars_jr[am][j_idx, r]
                        <= self.X_ag_dry_vars_jr[j, r],
                        name=f"const_ag_mam_dry_usage_{am}_{j}_{r}".replace(" ", "_"),
                    )
                    self.ag_management_constraints_r[r].append(constr)
                for r in lm_irr_r_vals:
                    constr = self.gurobi_model.addConstr(
                        self.X_ag_man_irr_vars_jr[am][j_idx, r]
                        <= self.X_ag_irr_vars_jr[j, r],
                        name=f"const_ag_mam_irr_usage_{am}_{j}_{r}".replace(" ", "_"),
                    )
                    self.ag_management_constraints_r[r].append(constr)

    def _add_agricultural_management_adoption_limit_constraints(self):
        """
        Add adoption limits constraints for agricultural management options.
        """
        print("    |__ Adding constraints for agricultural management adoption limits...")

        for am, am_j_list in self._input_data.am2j.items():
            for j_idx, j in enumerate(am_j_list):
                adoption_limit = self._input_data.ag_man_limits[am][j]

                # Sum of all usage of the AM option must be less than the limit
                ag_man_vars_sum = gp.quicksum(
                    self.X_ag_man_dry_vars_jr[am][j_idx, :]
                ) + gp.quicksum(self.X_ag_man_irr_vars_jr[am][j_idx, :])

                all_vars_sum = gp.quicksum(self.X_ag_dry_vars_jr[j, :]) + gp.quicksum(
                    self.X_ag_irr_vars_jr[j, :]
                )
                constr = self.gurobi_model.addConstr(
                    ag_man_vars_sum <= adoption_limit * all_vars_sum,
                    name=f"const_ag_mam_adoption_limit_{am}_{j}".replace(" ", "_"),
                )

                self.adoption_limit_constraints.append(constr)

    def _add_demand_penalty_constraints(self):
        """
        Constraints to penalise under and over production compared to demand.
        """
        print("    |__ Adding constraints for demand penalties...")
        
        self.ag_q_c = [gp.LinExpr(0) for _ in range(self._input_data.ncms)]
        for j in range(self._input_data.n_ag_lus):
            X_ag_dry_r = self.X_ag_dry_vars_jr[j, :]
            X_ag_irr_r = self.X_ag_irr_vars_jr[j, :]
            
            for p in range(self._input_data.nprs):
                if not self._input_data.lu2pr_pj[p, j]:
                    continue
                ag_q_p = (
                    gp.quicksum(
                        self._input_data.ag_q_mrp[0, :, p] * X_ag_dry_r
                    ) 
                    + gp.quicksum(
                        self._input_data.ag_q_mrp[1, :, p] * X_ag_irr_r
                    ) 
                )  
                
                for c in range(self._input_data.ncms):
                    if not self._input_data.pr2cm_cp[c, p]:
                        continue
                    self.ag_q_c[c] += ag_q_p
                        
                        
        self.ag_man_q_c = [gp.LinExpr(0) for _ in range(self._input_data.ncms)]
        for am, am_j_list in self._input_data.am2j.items():
            if not AG_MANAGEMENTS[am]:
                continue
            
            for j_idx,j in enumerate(am_j_list):
                X_ag_mam_dry_r = self.X_ag_man_dry_vars_jr[am][j_idx, :]
                X_ag_mam_irr_r = self.X_ag_man_irr_vars_jr[am][j_idx, :]
                
                for p in range(self._input_data.nprs):
                    if not self._input_data.lu2pr_pj[p, j]:
                        continue
                    ag_mam_q_p = (
                        gp.quicksum(
                            self._input_data.ag_man_q_mrp[am][0, :, p] * X_ag_mam_dry_r
                        ) 
                        + gp.quicksum(
                            self._input_data.ag_man_q_mrp[am][1, :, p] * X_ag_mam_irr_r
                        ) 
                    )  
                    
                    for c in range(self._input_data.ncms):
                        if not self._input_data.pr2cm_cp[c, p]:
                            continue
                        self.ag_man_q_c[c] += ag_mam_q_p


        self.non_ag_q_c = [gp.LinExpr(0) for _ in range(self._input_data.ncms)]
        for k,k_name in enumerate(NON_AG_LAND_USES):
            if not NON_AG_LAND_USES[k_name]:
                continue
            
            for c in range(self._input_data.ncms):
                self.non_ag_q_c[c] += gp.quicksum(
                    self._input_data.non_ag_q_crk[c, :, k] * self.X_non_ag_vars_kr[k, :]
                )
            

        # Total quantities in CM/c representation.
        self.total_q_exprs_c = [
            self.ag_q_c[c]
            + self.ag_man_q_c[c] 
            + self.non_ag_q_c[c] 
            for c in range(self._input_data.ncms)
        ]

        lower_bound_constraints = self.gurobi_model.addConstrs(
            (
                (self.total_q_exprs_c[c] - self._input_data.limits['demand_rescale'][c]) == self.V[c] 
                for c in range(self._input_data.ncms)
            ),  name="demand_soft_bound_lower"
        )

        # self.demand_penalty_constraints.extend(upper_bound_constraints.values())
        self.demand_penalty_constraints.extend(lower_bound_constraints.values())


    def _get_water_net_yield_expr_for_region(
        self,
        ind: np.ndarray,
    ) -> gp.LinExpr:
        """
        Get the Gurobi linear expression for the net water yield of a given region.
        """
        
        ag_exprs = []
        for j in range(self._input_data.n_ag_lus):
            ag_exprs.append(
                gp.quicksum(
                    self._input_data.ag_w_mrj[0, ind, j] * self.X_ag_dry_vars_jr[j, ind]
                )  # Dryland agriculture contribution
                + gp.quicksum(
                    self._input_data.ag_w_mrj[1, ind, j] * self.X_ag_irr_vars_jr[j, ind]
                )  # Irrigated agriculture contribution
            )
 
        ag_mam_exprs = []
        for am, am_j_list in self._input_data.am2j.items():
            if not AG_MANAGEMENTS[am]:
                continue
            
            for j_idx in range(len(am_j_list)):
                ag_mam_exprs.append(
                    gp.quicksum(
                        self._input_data.ag_man_w_mrj[am][0, ind, j_idx]
                        * self.X_ag_man_dry_vars_jr[am][j_idx, ind]
                    )  # Dryland alt. ag. management contributions
                    + gp.quicksum(
                        self._input_data.ag_man_w_mrj[am][1, ind, j_idx]
                        * self.X_ag_man_irr_vars_jr[am][j_idx, ind]
                    )  # Irrigated alt. ag. management contributions
                )

        non_ag_exprs = []
        for k in range(self._input_data.n_non_ag_lus):
            non_ag_exprs.append(
                gp.quicksum(
                    self._input_data.non_ag_w_rk[ind, k] * self.X_non_ag_vars_kr[k, ind]
                )  # Non-agricultural contribution
            )
        
        ag_contr = gp.quicksum(ag_exprs) 
        ag_man_contr = gp.quicksum(ag_mam_exprs)
        non_ag_contr = gp.quicksum(non_ag_exprs)
        return ag_contr + ag_man_contr + non_ag_contr


    def _add_water_usage_limit_constraints(self) -> None:
        
        if settings.WATER_LIMITS != "on": 
            print("    |__ TURNING OFF water usage constraints ...")
            return
        
        print("    |__ Adding constraints for water usage limits...")
        
        # Ensure water use remains below limit for each region
        for reg_idx, water_limit_rescale in self._input_data.limits["water_rescale"].items():
            
            w_limit_raw = water_limit_rescale * self._input_data.scale_factors['Water']
            ind = self._input_data.water_region_indices[reg_idx]
            reg_name = self._input_data.water_region_names[reg_idx]

            print(f"      |__ target is {w_limit_raw:15,.0f} ML for {reg_name}")

            self.water_nyiled_exprs[reg_idx] = self._get_water_net_yield_expr_for_region(ind)           # Water net yield inside LUTO study area

            if settings.WATER_CONSTRAINT_TYPE == "hard":
                constr = self.gurobi_model.addConstr(
                    self.water_nyiled_exprs[reg_idx] >= water_limit_rescale, 
                    name=f"water_yield_limit_{reg_name}".replace(" ", "_")
                )
            elif settings.WATER_CONSTRAINT_TYPE == "soft":
                constr = self.gurobi_model.addConstr(
                    water_limit_rescale - self.water_nyiled_exprs[reg_idx] <= self.W[reg_idx - 1],     # region index starts from 1
                    name=f"water_yield_limit_{reg_name.replace(' ', '_')}"
                )
            else:
                raise ValueError(
                    "Unknown choice for `WATER_CONSTRAINT_TYPE` setting: must be either 'hard' or 'soft'"
                ) 
                
            self.water_limit_constraints.append(constr)


    def _get_total_ghg_expr(self) -> gp.LinExpr:
        # Pre-calculate the coefficients for each variable,
        # both for regular culture and alternative agr. management options
        g_dry_coeff = (
            self._input_data.ag_g_mrj[0, :, :] + self._input_data.ag_ghg_t_mrj[0, :, :]
        )
        g_irr_coeff = (
            self._input_data.ag_g_mrj[1, :, :] + self._input_data.ag_ghg_t_mrj[1, :, :]
        )

        ghg_ag_exprs =[]
        for j in range(self._input_data.n_ag_lus):
            ghg_ag_exprs.append(
                gp.quicksum(
                    g_dry_coeff[:, j] * self.X_ag_dry_vars_jr[j, :]
                )
                + gp.quicksum(
                    g_irr_coeff[:, j] * self.X_ag_irr_vars_jr[j, :]
                )
            )
            
        ghg_ag_man_exprs = []
        for am, am_j_list in self._input_data.am2j.items():
            if not AG_MANAGEMENTS[am]:
                continue
            
            for j_idx in range(len(am_j_list)):
                ghg_ag_man_exprs.append(
                    gp.quicksum(
                        self._input_data.ag_man_g_mrj[am][0, :, j_idx]
                        * self.X_ag_man_dry_vars_jr[am][j_idx, :]
                    )  
                    + gp.quicksum(
                        self._input_data.ag_man_g_mrj[am][1, :, j_idx]
                        * self.X_ag_man_irr_vars_jr[am][j_idx, :]
                    )  
                )
                
        ghg_non_ag_exprs = []
        for k,k_name in enumerate(NON_AG_LAND_USES):
            if not NON_AG_LAND_USES[k_name]:
                continue
            ghg_non_ag_exprs.append(
                gp.quicksum(
                    self._input_data.non_ag_g_rk[:, k] * self.X_non_ag_vars_kr[k, :]
                )
            )
            
        self.ghg_ag_contr = gp.quicksum(ghg_ag_exprs)
        self.ghg_ag_man_contr = gp.quicksum(ghg_ag_man_exprs)
        self.ghg_non_ag_contr = gp.quicksum(ghg_non_ag_exprs)    
        
        return self.ghg_ag_contr + self.ghg_ag_man_contr + self.ghg_non_ag_contr + self._input_data.offland_ghg

    def _add_ghg_emissions_limit_constraints(self):
        """
        Add either hard or soft GHG constraints depending on settings.GHG_CONSTRAINT_TYPE
        """
        if settings.GHG_EMISSIONS_LIMITS == "off":
            print("    |__ TURNING OFF GHG emissions constraints ...")
            return
                
        ghg_limit_raw = self._input_data.limits["ghg"]
        ghg_limit_rescale = self._input_data.limits["ghg_rescale"]
        self.ghg_expr = self._get_total_ghg_expr()

        if settings.GHG_CONSTRAINT_TYPE == "hard":
            print(f"    |__ Adding constraints <hard> for GHG emissions: {ghg_limit_raw:,.0f} tCO2e")
            self.ghg_consts_ub = self.gurobi_model.addConstr(
                self.ghg_expr <= ghg_limit_rescale,
                name="ghg_emissions_limit_ub",
            )
        elif settings.GHG_CONSTRAINT_TYPE == "soft":
            print(f"    |__ Adding <soft> constraints for GHG emissions: {ghg_limit_raw:,.0f} tCO2e")
            self.ghg_consts_soft.append(
                self.gurobi_model.addConstr(
                    self.ghg_expr - ghg_limit_rescale <= self.E,
                    name="ghg_emissions_limit_soft_ub",
                )
            )
            self.ghg_consts_soft.append(
                self.gurobi_model.addConstr(
                    ghg_limit_rescale - self.ghg_expr <= self.E,
                    name="ghg_emissions_limit_soft_lb",
                )
            )
        else:
            raise ValueError(
                "    Unknown choice for `GHG_CONSTRAINT_TYPE` setting: must be either 'hard' or 'soft'"
            )
            
            
    def _add_biodiversity_constraints(self) -> None:
        print("    |__ Adding constraints for biodiversity...")
        self._add_GBF2_constraints()
        self._add_GBF3_major_vegetation_group_limit_constraints()
        self._add_GBF4_snes_constraints()
        self._add_GBF4_ecnes_constraints()
        self._add_GBF8_constraints()


    def _add_GBF2_constraints(self) -> None:
        
        if settings.BIODIVERSITY_TARGET_GBF_2 == "off":
            print("      |__ TURNING OFF constraints for biodiversity GBF 2...")
            return
        
        bio_ag_exprs = []
        bio_ag_man_exprs = []
        bio_non_ag_exprs = []
        
        for j in range(self._input_data.n_ag_lus):
            ind_dry = np.intersect1d(self._input_data.ag_lu2cells[0, j], self._input_data.priority_degraded_mask_idx)
            ind_irr = np.intersect1d(self._input_data.ag_lu2cells[1, j], self._input_data.priority_degraded_mask_idx)
            bio_ag_exprs.append(
                gp.quicksum(
                    self._input_data.GBF2_raw_priority_degraded_area_r[ind_dry]
                    * self._input_data.biodiv_contr_ag_j[j]
                    * self.X_ag_dry_vars_jr[j, ind_dry]
                )
                + gp.quicksum(
                    self._input_data.GBF2_raw_priority_degraded_area_r[ind_irr]
                    * self._input_data.biodiv_contr_ag_j[j]
                    * self.X_ag_irr_vars_jr[j, ind_irr]
                ) 
            )
        for am, am_j_list in self._input_data.am2j.items():
            if not AG_MANAGEMENTS[am]:
                continue
            for j_idx in range(len(am_j_list)):
                
                ind_dry = np.intersect1d(self._input_data.ag_lu2cells[0, j_idx], self._input_data.priority_degraded_mask_idx)
                ind_irr = np.intersect1d(self._input_data.ag_lu2cells[1, j_idx], self._input_data.priority_degraded_mask_idx)
                bio_ag_man_exprs.append(
                    gp.quicksum(self._input_data.GBF2_raw_priority_degraded_area_r[ind_dry]
                        * self._input_data.biodiv_contr_ag_man[am][j_idx][ind_dry]
                        * self.X_ag_man_dry_vars_jr[am][j_idx, ind_dry])
                    + gp.quicksum(
                        self._input_data.GBF2_raw_priority_degraded_area_r[ind_irr]
                        * self._input_data.biodiv_contr_ag_man[am][j_idx][ind_irr]
                        * self.X_ag_man_irr_vars_jr[am][j_idx, ind_irr]
                    )
                )  
        for k in range(self._input_data.n_non_ag_lus):
            ind = np.intersect1d(self._input_data.non_ag_lu2cells[k], self._input_data.priority_degraded_mask_idx)
            bio_non_ag_exprs.append(
                gp.quicksum(
                    self._input_data.GBF2_raw_priority_degraded_area_r[ind]
                    * self._input_data.biodiv_contr_non_ag_k[k]
                    * self.X_non_ag_vars_kr[k, ind]
                )
            )

        self.bio_GBF2_expr = (
            gp.quicksum(bio_ag_exprs) 
            + gp.quicksum(bio_ag_man_exprs) 
            + gp.quicksum(bio_non_ag_exprs)
        ) 


        print(f'      |__ Adding constraints for biodiversity GBF 2: {self._input_data.limits["GBF2"]:15,.0f}')
        
        self.bio_GBF2_constrs = self.gurobi_model.addConstr(
            self.bio_GBF2_expr >= self._input_data.limits["GBF2_rescale"], 
            name="bio_GBF2_priority_degraded_area_limit"
        )


    def _add_GBF3_major_vegetation_group_limit_constraints(self) -> None:
        if settings.BIODIVERSITY_TARGET_GBF_3 == "off":
            print("      |__ TURNING OFF constraints for biodiversity GBF 3 (major vegetation groups)")
            return

        v_limits = self._input_data.limits["GBF3_rescale"]
        v_names = self._input_data.GBF3_names
        v_ind = self._input_data.GBF3_ind

        print(f"      Adding constraints for biodiversity GBF 3...")

        for v, v_area_lb_rescale in enumerate(v_limits):
            
            v_area_lb_raw = v_area_lb_rescale * self._input_data.scale_factors['GBF3']
            
            if v_area_lb_raw == 0:
                print(f"        |__ target is {v_area_lb_raw:15,.0f} for {v_names[v]} (skipped modelling)  ")
                continue
            
            ind = v_ind[v]
            MVG_raw_area_r = self._input_data.GBF3_raw_MVG_area_vr[v, ind]

            ag_contr = gp.quicksum(
                gp.quicksum(
                    MVG_raw_area_r
                    * self._input_data.biodiv_contr_ag_j[j]
                    * self.X_ag_dry_vars_jr[j, ind]
                )  # Dryland agriculture contribution
                + gp.quicksum(
                    MVG_raw_area_r
                    * self._input_data.biodiv_contr_ag_j[j]
                    * self.X_ag_irr_vars_jr[j, ind]
                )  # Irrigated agriculture contribution
                for j in range(self._input_data.n_ag_lus)
            )

            ag_man_contr = gp.quicksum(
                gp.quicksum(
                    MVG_raw_area_r
                    * self._input_data.biodiv_contr_ag_man[am][j_idx][ind]
                    * self.X_ag_man_dry_vars_jr[am][j_idx, ind]
                )  # Dryland alt. ag. management contributions
                + gp.quicksum(
                    MVG_raw_area_r
                    * self._input_data.biodiv_contr_ag_man[am][j_idx][ind]
                    * self.X_ag_man_irr_vars_jr[am][j_idx, ind]
                )  # Irrigated alt. ag. management contributions
                for am, am_j_list in self._input_data.am2j.items()
                for j_idx in range(len(am_j_list))
            )

            non_ag_contr = gp.quicksum(
                gp.quicksum(
                    MVG_raw_area_r
                    * self._input_data.biodiv_contr_non_ag_k[k]
                    * self.X_non_ag_vars_kr[k, ind]
                )  # Non-agricultural contribution
                for k in range(self._input_data.n_non_ag_lus)
            )


            self.bio_GBF3_exprs[v] = ag_contr + ag_man_contr + non_ag_contr

            print(f"        |__ target is {v_area_lb_raw:15,.0f} for {v_names[v]} ")
            self.bio_GBF3_constrs[v] = self.gurobi_model.addConstr(
                self.bio_GBF3_exprs[v] >= v_area_lb_rescale,
                name=f"bio_GBF3_limit_{v_names[v]}".replace(" ", "_")
            )


    def _add_GBF4_snes_constraints(self) -> None:
        if settings.BIODIVERSITY_TARGET_GBF_4_SNES != "on":
            print('      |__ TURNING OFF constraints for biodiversity GBF 4 SNES...')
            return
        
        x_limits = self._input_data.limits["GBF4_SNES_rescale"]
        x_names = self._input_data.GBF4_SNES_names

        print(f"      |__ Adding constraints for biodiversity GBF 4 SNES...")
        
        for x, x_area_lb_rescale in enumerate(x_limits):
            x_area_lb_raw = x_area_lb_rescale * self._input_data.scale_factors['GBF4_SNES']
            ind = np.where(self._input_data.GBF4_SNES_xr[x] > 0)[0]

            if ind.size == 0:
                print(
                    f"        |__ WARNING: SNES species NOT added because of empty layer for {x_names[x]}")
                continue
            
            ag_contr = gp.quicksum(
                gp.quicksum(
                    self._input_data.GBF4_SNES_xr[x, ind]
                    * self._input_data.biodiv_contr_ag_j[j]
                    * self.X_ag_dry_vars_jr[j, ind]
                )  # Dryland agriculture contribution
                + gp.quicksum(
                    self._input_data.GBF4_SNES_xr[x, ind]
                    * self._input_data.biodiv_contr_ag_j[j]
                    * self.X_ag_irr_vars_jr[j, ind]
                )  # Irrigated agriculture contribution
                for j in range(self._input_data.n_ag_lus)
            )

            ag_man_contr = gp.quicksum(
                gp.quicksum(
                    self._input_data.GBF4_SNES_xr[x, ind]
                    * self._input_data.biodiv_contr_ag_man[am][j_idx][ind]
                    * self.X_ag_man_dry_vars_jr[am][j_idx, ind]
                )  # Dryland alt. ag. management contributions
                + gp.quicksum(
                    self._input_data.GBF4_SNES_xr[x, ind]
                    * self._input_data.biodiv_contr_ag_man[am][j_idx][ind]
                    * self.X_ag_man_irr_vars_jr[am][j_idx, ind]
                )  # Irrigated alt. ag. management contributions
                for am, am_j_list in self._input_data.am2j.items()
                for j_idx in range(len(am_j_list))
            )

            non_ag_contr = gp.quicksum(
                gp.quicksum(
                    self._input_data.GBF4_SNES_xr[x, ind]
                    * self._input_data.biodiv_contr_non_ag_k[k]
                    * self.X_non_ag_vars_kr[k, ind]
                )  # Non-agricultural contribution
                for k in range(self._input_data.n_non_ag_lus)
            )

            self.bio_GBF4_SNES_exprs[x] = ag_contr + ag_man_contr + non_ag_contr

            print(f"        |__ target is {x_area_lb_raw:15,.0f} for {x_names[x]}")
            self.bio_GBF4_SNES_constrs[x] = self.gurobi_model.addConstr(
                self.bio_GBF4_SNES_exprs[x] >= x_area_lb_rescale,
                name=f"bio_GBF4_SNES_limit_{x_names[x]}".replace(" ", "_"),
            )

    def _add_GBF4_ecnes_constraints(self) -> None:
        if settings.BIODIVERSITY_TARGET_GBF_4_ECNES != "on":
            print('      |__ TURNING OFF constraints for biodiversity GBF 4 ECNES...')
            return
        
        x_limits = self._input_data.limits["GBF4_ECNES_rescale"]
        x_names = self._input_data.GBF4_ECNES_names

        print(f"      |__ Adding constraints for biodiversity GBF 4 ECNES...")
        
        for x, x_area_lb_rescale in enumerate(x_limits):
            x_area_lb_raw = x_area_lb_rescale * self._input_data.scale_factors['GBF4_ECNES']
            ind = np.where(self._input_data.GBF4_ECNES_xr[x] > 0)[0]

            if ind.size == 0:
                print(
                    f"        |__ WARNING: ECNES species was NOT added because of empty layer for {x_names[x]}")
                continue
            
            ag_contr = gp.quicksum(
                gp.quicksum(
                    self._input_data.GBF4_ECNES_xr[x, ind]
                    * self._input_data.biodiv_contr_ag_j[j]
                    * self.X_ag_dry_vars_jr[j, ind]
                )  # Dryland agriculture contribution
                + gp.quicksum(
                    self._input_data.GBF4_ECNES_xr[x, ind]
                    * self._input_data.biodiv_contr_ag_j[j]
                    * self.X_ag_irr_vars_jr[j, ind]
                )  # Irrigated agriculture contribution
                for j in range(self._input_data.n_ag_lus)
            )

            ag_man_contr = gp.quicksum(
                gp.quicksum(
                    self._input_data.GBF4_ECNES_xr[x, ind]
                    * self._input_data.biodiv_contr_ag_man[am][j_idx][ind]
                    * self.X_ag_man_dry_vars_jr[am][j_idx, ind]
                )  # Dryland alt. ag. management contributions
                + gp.quicksum(
                    self._input_data.GBF4_ECNES_xr[x, ind]
                    * self._input_data.biodiv_contr_ag_man[am][j_idx][ind]
                    * self.X_ag_man_irr_vars_jr[am][j_idx, ind]
                )  # Irrigated alt. ag. management contributions
                for am, am_j_list in self._input_data.am2j.items()
                for j_idx in range(len(am_j_list))
            )

            non_ag_contr = gp.quicksum(
                gp.quicksum(
                    self._input_data.GBF4_ECNES_xr[x, ind]
                    * self._input_data.biodiv_contr_non_ag_k[k]
                    * self.X_non_ag_vars_kr[k, ind]
                )  # Non-agricultural contribution
                for k in range(self._input_data.n_non_ag_lus)
            )

            self.bio_GBF4_ECNES_exprs[x] = ag_contr + ag_man_contr + non_ag_contr


            print(f"        |__ target is {x_area_lb_raw:15,.0f} for {x_names[x]} ")
            self.bio_GBF4_ECNES_constrs[x] = self.gurobi_model.addConstr(
                self.bio_GBF4_ECNES_exprs[x] >= x_area_lb_rescale,
                name=f"bio_GBF4_ECNES_limit_{x_names[x]}".replace(" ", "_")
            )


    def _add_GBF8_constraints(self) -> None:
                
        if settings.BIODIVERSITY_TARGET_GBF_8 != "on":
            print('      |__ TURNING OFF constraints for biodiversity GBF 8...')
            return
        
        s_limits = self._input_data.limits["GBF8_rescale"]
        s_names = self._input_data.GBF8_species_names
        s_ind = self._input_data.GBF8_species_indices

        print(f"      |__ Adding constraints for biodiversity GBF 8...")
        
        for s, s_area_lb_rescale in enumerate(s_limits):
            
            ind = s_ind[s]
            s_area_lb_raw = s_area_lb_rescale * self._input_data.scale_factors['GBF8']
            GBF8_raw_area_r = self._input_data.GBF8_raw_species_area_sr[s, ind]
            
            ag_contr = gp.quicksum(
                gp.quicksum(
                    GBF8_raw_area_r
                    * self._input_data.biodiv_contr_ag_j[j]
                    * self.X_ag_dry_vars_jr[j, ind]
                )  # Dryland agriculture contribution
                + gp.quicksum(
                    GBF8_raw_area_r
                    * self._input_data.biodiv_contr_ag_j[j]
                    * self.X_ag_irr_vars_jr[j, ind]
                )  # Irrigated agriculture contribution
                for j in range(self._input_data.n_ag_lus)
            )

            ag_man_contr = gp.quicksum(
                gp.quicksum(
                    GBF8_raw_area_r
                    * self._input_data.biodiv_contr_ag_man[am][j_idx][ind]
                    * self.X_ag_man_dry_vars_jr[am][j_idx, ind]
                )  # Dryland alt. ag. management contributions
                + gp.quicksum(
                    GBF8_raw_area_r
                    * self._input_data.biodiv_contr_ag_man[am][j_idx][ind]
                    * self.X_ag_man_irr_vars_jr[am][j_idx, ind]
                )  # Irrigated alt. ag. management contributions
                for am, am_j_list in self._input_data.am2j.items()
                for j_idx in range(len(am_j_list))
            )

            non_ag_contr = gp.quicksum(
                gp.quicksum(
                    GBF8_raw_area_r
                    * self._input_data.biodiv_contr_non_ag_k[k]
                    * self.X_non_ag_vars_kr[k, ind]
                )  # Non-agricultural contribution
                for k in range(self._input_data.n_non_ag_lus)
            )

            # Divide by constant to reduce strain on the constraint matrix range
            self.bio_GBF8_exprs[s] = ag_contr + ag_man_contr + non_ag_contr
    
            print(f"        |__ target is {s_area_lb_raw:15,.0f} for {s_names[s]}")
            self.bio_GBF8_constrs[s] = self.gurobi_model.addConstr(
                self.bio_GBF8_exprs[s] >= s_area_lb_rescale,
                name=f"bio_GBF8_limit_{s_names[s]}".replace(" ", "_"),
            )


    def _add_regional_adoption_constraints(self) -> None:

        if settings.REGIONAL_ADOPTION_CONSTRAINTS != "on":
            print("      |__ TURNING OFF constraints for regional adoption...")
            return
                
        # Add adoption constraints for agricultural land uses
        reg_adopt_limits = self._input_data.limits["ag_regional_adoption"]
        for reg_id, j, lu_name, reg_ind, reg_area_limit in reg_adopt_limits:
            print(f"       |__ Adding constraints for {lu_name} in {settings.REGIONAL_ADOPTION_ZONE} region {reg_id} >= {reg_area_limit:,.0f} HA...")
            reg_expr = (
                  gp.quicksum(self._input_data.real_area[reg_ind] * self.X_ag_dry_vars_jr[j, reg_ind])
                + gp.quicksum(self._input_data.real_area[reg_ind] * self.X_ag_irr_vars_jr[j, reg_ind])
            )
            self.regional_adoption_constrs.append(self.gurobi_model.addConstr(reg_expr <= reg_area_limit, name=f"reg_adopt_limit_ag_{lu_name}_{reg_id}"))
        
        # Add adoption constraints for non-agricultural land uses
        reg_adopt_limits = self._input_data.limits["non_ag_regional_adoption"]
        for reg_id, k, lu_name, reg_ind, reg_area_limit in reg_adopt_limits:
            print(f"       |__ Adding constraints for {lu_name} in {settings.REGIONAL_ADOPTION_ZONE} region {reg_id} >= {reg_area_limit:,.0f} HA...")
            reg_expr = gp.quicksum(self._input_data.real_area[reg_ind] * self.X_non_ag_vars_kr[k, reg_ind])
            self.regional_adoption_constrs.append(self.gurobi_model.addConstr(reg_expr <= reg_area_limit, name=f"reg_adopt_limit_non_ag_{lu_name}_{reg_id}"))




    def update_formulation(
        self,
        input_data: SolverInputData,
        d_c: np.array,
        old_ag_x_mrj: np.ndarray,
        old_ag_man_lb_mrj: dict,
        old_non_ag_x_rk: np.ndarray,
        old_non_ag_lb_rk: np.ndarray,
        old_lumap: np.array,
        current_lumap: np.array,
        old_lmmap: np.array,
        current_lmmap: np.array,
    ):
        """
        Dynamically updates the existing formulation based on new input data and demands.
        """
        self._input_data = input_data
        self._input_data.limits['demand'] = d_c

        print("Updating variables...", flush=True)
        updated_cells = self._update_variables(
            old_ag_x_mrj,
            old_ag_man_lb_mrj,
            old_non_ag_x_rk,
            old_non_ag_lb_rk,
            old_lumap,
            current_lumap,
            old_lmmap,
            current_lmmap,
        )
        print("Updating constraints...", flush=True)
        self._update_constraints(updated_cells)

        print("Updating objective function...", flush=True)
        self._setup_objective()

    def _update_variables(
        self,
        old_ag_x_mrj: np.ndarray,
        old_ag_man_lb_mrj: dict,
        old_non_ag_x_rk: np.ndarray,
        old_non_ag_lb_rk: np.ndarray,
        old_lumap: np.array,
        current_lumap: np.array,
        old_lmmap: np.array,
        current_lmmap: np.array,
    ):
        """
        Updates the variables only for cells that have changed land use or land management.
        Returns an array of cells that have been updated.
        """
        # metrics
        num_cells_skipped = 0
        updated_cells = []

        for r in range(self._input_data.ncells):
            old_j = old_lumap[r]
            new_j = current_lumap[r]
            old_m = old_lmmap[r]
            new_m = current_lmmap[r]

            if (
                old_j == new_j
                and old_m == new_m
                and (old_ag_x_mrj[:, r, :] == self._input_data.ag_x_mrj[:, r, :]).all()
                and (old_non_ag_x_rk[r, :] == self._input_data.non_ag_x_rk[r, :]).all()
                and all(
                    old_non_ag_lb_rk[r, k] == self._input_data.non_ag_lb_rk[r, k]
                    for k, k_name in enumerate(NON_AG_LAND_USES)
                    if not NON_AG_LAND_USES_REVERSIBLE[k_name]
                )
                and all(
                    (old_ag_man_lb_mrj.get(am)[:, r, :] == self._input_data.ag_man_lb_mrj.get(am)[:, r, :]).all()
                    for am in (i for i in AG_MANAGEMENTS if AG_MANAGEMENTS[i])
                    if not AG_MANAGEMENTS_REVERSIBLE[am]
                )
            ):
                # cell has not changed between years. No need to update variables
                num_cells_skipped += 1
                continue

            # agricultural land usage
            self.gurobi_model.remove(
                list(self.X_ag_dry_vars_jr[:, r][np.where(self.X_ag_dry_vars_jr[:, r])])
            )
            self.gurobi_model.remove(
                list(self.X_ag_irr_vars_jr[:, r][np.where(self.X_ag_irr_vars_jr[:, r])])
            )
            self.X_ag_dry_vars_jr[:, r] = np.zeros(self._input_data.n_ag_lus)
            self.X_ag_irr_vars_jr[:, r] = np.zeros(self._input_data.n_ag_lus)
            for j in range(self._input_data.n_ag_lus):
                if self._input_data.ag_x_mrj[0, r, j]:
                    self.X_ag_dry_vars_jr[j, r] = self.gurobi_model.addVar(
                        ub=1, name=f"X_ag_dry_{j}_{r}"
                    )

                if self._input_data.ag_x_mrj[1, r, j]:
                    self.X_ag_irr_vars_jr[j, r] = self.gurobi_model.addVar(
                        ub=1, name=f"X_ag_irr_{j}_{r}"
                    )

            # non-agricultural land usage
            self.gurobi_model.remove(
                list(self.X_non_ag_vars_kr[:, r][np.where(self.X_non_ag_vars_kr[:, r])])
            )
            self.X_non_ag_vars_kr[:, r] = np.zeros(self._input_data.n_non_ag_lus)
            for k, k_name in enumerate(NON_AG_LAND_USES):
                if not NON_AG_LAND_USES[k_name]:
                    continue

                if self._input_data.non_ag_x_rk[r, k]:
                    x_lb = (
                        0
                        if NON_AG_LAND_USES_REVERSIBLE[k_name]
                        else self._input_data.non_ag_lb_rk[r, k]
                    )
                    self.X_non_ag_vars_kr[k, r] = self.gurobi_model.addVar(
                        lb=x_lb,
                        ub=self._input_data.non_ag_x_rk[r, k],
                        name=f"X_non_ag_{k}_{r}",
                    )

            # agricultural management
            for am, am_j_list in self._input_data.am2j.items():
                # remove old am variables
                self.gurobi_model.remove(
                    list(
                        self.X_ag_man_dry_vars_jr[am][:, r][
                            np.where(self.X_ag_man_dry_vars_jr[am][:, r])
                        ]
                    )
                )
                self.gurobi_model.remove(
                    list(
                        self.X_ag_man_irr_vars_jr[am][:, r][
                            np.where(self.X_ag_man_irr_vars_jr[am][:, r])
                        ]
                    )
                )
                self.X_ag_man_dry_vars_jr[am][:, r] = np.zeros(len(am_j_list))
                self.X_ag_man_irr_vars_jr[am][:, r] = np.zeros(len(am_j_list))

            for m, j in self._input_data.cells2ag_lu[r]:
                # replace am variables
                for am in self._input_data.j2am[j]:
                    if not AG_MANAGEMENTS[am]:
                        continue

                    # Get snake_case version of the AM name for the variable name
                    am_name = am.lower().replace(" ", "_")

                    x_lb = (
                        0
                        if AG_MANAGEMENTS_REVERSIBLE[am]
                        else self._input_data.ag_man_lb_mrj[am][m, r, j]
                    )
                    m_str = "dry" if m == 0 else "irr"
                    var_name = f"X_ag_man_{m_str}_{am_name}_{j}_{r}"

                    j_idx = self._input_data.am2j[am].index(j)
                    if m == 0:
                        self.X_ag_man_dry_vars_jr[am][j_idx, r] = (
                            self.gurobi_model.addVar(
                                lb=x_lb,
                                ub=1,
                                name=var_name,
                            )
                        )
                    else:
                        self.X_ag_man_irr_vars_jr[am][j_idx, r] = (
                            self.gurobi_model.addVar(
                                lb=x_lb,
                                ub=1,
                                name=var_name,
                            )
                        )

            updated_cells.append(r)

        updated_cells = np.array(updated_cells)
        print(f"    ...skipped {num_cells_skipped} cells, updated {len(updated_cells)} cells.\n")
        return updated_cells

    def _update_constraints(self, updated_cells: np.array):
        if len(updated_cells) == 0:
            print("No constraints need updating.")
            return

        print("  ...removing existing constraints...\n")
        for r in updated_cells:
            self.gurobi_model.remove(self.cell_usage_constraint_r.pop(r, []))
            self.gurobi_model.remove(self.ag_management_constraints_r.pop(r, []))

        self.gurobi_model.remove(self.adoption_limit_constraints)
        self.gurobi_model.remove(self.demand_penalty_constraints)
        if self.bio_GBF2_constrs is not None:
            self.gurobi_model.remove(self.bio_GBF2_constrs)
        if self.water_limit_constraints:
            self.gurobi_model.remove(self.water_limit_constraints)
        if self.bio_GBF3_constrs:
            for constr in self.bio_GBF3_constrs.values():
                self.gurobi_model.remove(constr)
        if self.bio_GBF4_SNES_constrs:
            for constr in self.bio_GBF4_SNES_constrs.values():
                self.gurobi_model.remove(constr)
        if self.bio_GBF4_ECNES_constrs:
            for constr in self.bio_GBF4_ECNES_constrs.values():
                self.gurobi_model.remove(constr)
        if self.bio_GBF8_constrs:
            for constr in self.bio_GBF8_constrs.values():
                self.gurobi_model.remove(constr)
        

        self.adoption_limit_constraints = []
        self.demand_penalty_constraints = []
        self.water_limit_constraints = []
        self.bio_GBF3_exprs = {}
        self.bio_GBF3_constrs = {}
        self.bio_GBF8_exprs = {}
        self.bio_GBF8_constrs = {}
        self.bio_GBF4_SNES_exprs = {}
        self.bio_GBF4_SNES_constrs = {}
        self.bio_GBF4_ECNES_exprs = {}
        self.bio_GBF4_ECNES_constrs = {}

        if self.ghg_consts_ub is not None:
            self.gurobi_model.remove(self.ghg_consts_ub)
            self.ghg_consts_ub = None

        if self.ghg_consts_lb is not None:
            self.gurobi_model.remove(self.ghg_consts_lb)
            self.ghg_consts_lb = None

        if len(self.ghg_consts_soft) > 0:
            for constr in self.ghg_consts_soft:
                self.gurobi_model.remove(constr)
            self.ghg_consts_soft = []

        if self.regional_adoption_constrs:
            self.gurobi_model.remove(self.regional_adoption_constrs)

        self.regional_adoption_constrs = []

        self._add_cell_usage_constraints(updated_cells)
        self._add_agricultural_management_constraints(updated_cells)
        self._add_agricultural_management_adoption_limit_constraints()
        self._add_demand_penalty_constraints()
        self._add_water_usage_limit_constraints()
        self._add_ghg_emissions_limit_constraints()
        self._add_biodiversity_constraints()
        self._add_regional_adoption_constraints()

    def solve(self) -> SolverSolution:
        print("Starting solve...\n")

        # Magic.
        self.gurobi_model.optimize()

        print("Completed solve, collecting results...\n", flush=True)

        prod_data = {}  # Dictionary that stores information about production and GHG emissions for the write module

        # Collect optimised decision variables in one X_mrj Numpy array.
        X_dry_sol_rj = np.zeros(
            (self._input_data.ncells, self._input_data.n_ag_lus)
        ).astype(np.float32)
        X_irr_sol_rj = np.zeros(
            (self._input_data.ncells, self._input_data.n_ag_lus)
        ).astype(np.float32)
        non_ag_X_sol_rk = np.zeros(
            (self._input_data.ncells, self._input_data.n_non_ag_lus)
        ).astype(np.float32)
        am_X_dry_sol_rj = {
            am: np.zeros((self._input_data.ncells, self._input_data.n_ag_lus)).astype(
                np.float32
            )
            for am in self._input_data.am2j
        }
        am_X_irr_sol_rj = {
            am: np.zeros((self._input_data.ncells, self._input_data.n_ag_lus)).astype(
                np.float32
            )
            for am in self._input_data.am2j
        }

        # Get agricultural results
        for j in range(self._input_data.n_ag_lus):
            for r in self._input_data.ag_lu2cells[0, j]:
                X_dry_sol_rj[r, j] = self.X_ag_dry_vars_jr[j, r].X
            for r in self._input_data.ag_lu2cells[1, j]:
                X_irr_sol_rj[r, j] = self.X_ag_irr_vars_jr[j, r].X

        # Get non-agricultural results
        for k, lu in enumerate(settings.NON_AG_LAND_USES):
            if not settings.NON_AG_LAND_USES[lu]:
                non_ag_X_sol_rk[:, k] = np.zeros(self._input_data.ncells)
                continue

            for r in self._input_data.non_ag_lu2cells[k]:
                non_ag_X_sol_rk[r, k] = self.X_non_ag_vars_kr[k, r].X

        # Get agricultural management results
        for am, am_j_list in self._input_data.am2j.items():
            for j_idx, j in enumerate(am_j_list):
                eligible_dry_cells = self._input_data.ag_lu2cells[0, j]
                eligible_irr_cells = self._input_data.ag_lu2cells[1, j]

                if am == "Savanna Burning":
                    eligible_dry_cells = np.intersect1d(
                        eligible_dry_cells, self._input_data.savanna_eligible_r
                    )
                    eligible_irr_cells = np.intersect1d(
                        eligible_irr_cells, self._input_data.savanna_eligible_r
                    )

                for r in eligible_dry_cells:
                    am_X_dry_sol_rj[am][r, j] = self.X_ag_man_dry_vars_jr[am][
                        j_idx, r
                    ].X
                for r in eligible_irr_cells:
                    am_X_irr_sol_rj[am][r, j] = self.X_ag_man_irr_vars_jr[am][
                        j_idx, r
                    ].X

        """Note that output decision variables are mostly 0 or 1 but in some cases they are somewhere in between which creates issues
            when converting to maps etc. as individual cells can have non-zero values for multiple land-uses and land management type.
            This code creates a boolean X_mrj output matrix and ensure that each cell has one and only one land-use and land management"""

        # Process agricultural land usage information
        # Stack dryland and irrigated decision variables
        ag_X_mrj = np.stack((X_dry_sol_rj, X_irr_sol_rj))  # Float32
        ag_X_mrj_processed = ag_X_mrj

        ## Note - uncomment the following block of code to revert the processed agricultural variables to be binary.

        # ag_X_mrj_shape = ag_X_mrj.shape

        # # Reshape so that cells are along the first axis and land management and use are flattened along second axis i.e. (XXXXXXX,  56)
        # ag_X_mrj_processed = np.moveaxis(ag_X_mrj, 1, 0)
        # ag_X_mrj_processed = ag_X_mrj_processed.reshape(ag_X_mrj_processed.shape[0], -1)

        # # Boolean matrix where the maximum value for each cell across all land management types and land uses is True
        # ag_X_mrj_processed = ag_X_mrj_processed.argmax(axis=1)[:, np.newaxis] == range(
        #     ag_X_mrj_processed.shape[1]
        # )

        # # Reshape to mrj structure
        # ag_X_mrj_processed = ag_X_mrj_processed.reshape(
        #     (ag_X_mrj_shape[1], ag_X_mrj_shape[0], ag_X_mrj_shape[2])
        # )
        # ag_X_mrj_processed = np.moveaxis(ag_X_mrj_processed, 0, 1)

        # Process non-agricultural land usage information
        # Boolean matrix where the maximum value for each cell across all non-ag LUs is True
        non_ag_X_rk_processed = non_ag_X_sol_rk.argmax(axis=1)[:, np.newaxis] == range(
            self._input_data.n_non_ag_lus
        )

        # Make land use and land management maps
        # Vector indexed by cell that denotes whether the cell is non-agricultural land (True) or agricultural land (False)
        non_ag_bools_r = non_ag_X_sol_rk.max(axis=1) > ag_X_mrj.max(axis=(0, 2))

        # Update processed variables accordingly
        ag_X_mrj_processed[:, non_ag_bools_r, :] = False
        non_ag_X_rk_processed[~non_ag_bools_r, :] = False

        # Process agricultural management variables
        # Repeat the steps for the regular agricultural management variables
        ag_man_X_mrj_processed = {}
        for am in self._input_data.am2j:
            ag_man_processed = np.stack((am_X_dry_sol_rj[am], am_X_irr_sol_rj[am]))

            ## Note - uncomment the following block of code to revert the processed AM variables to be binary.

            # ag_man_X_shape = ag_man_processed.shape

            # ag_man_processed = np.moveaxis(ag_man_processed, 1, 0)
            # ag_man_processed = ag_man_processed.reshape(ag_man_processed.shape[0], -1)

            # ag_man_processed = (
            #        ag_man_processed.argmax(axis = 1)[:, np.newaxis]
            #     == range(ag_man_processed.shape[1])
            # )
            # ag_man_processed = ag_man_processed.reshape(
            #     (ag_man_X_shape[1], ag_man_X_shape[0], ag_man_X_shape[2])
            # )
            # ag_man_processed = np.moveaxis(ag_man_processed, 0, 1)
            ag_man_X_mrj_processed[am] = ag_man_processed

        # Calculate 1D array (maps) of land-use and land management, considering only agricultural LUs
        lumap = ag_X_mrj_processed.sum(axis=0).argmax(axis=1).astype("int8")
        lmmap = ag_X_mrj_processed.sum(axis=2).argmax(axis=0).astype("int8")

        # Update lxmaps and processed variable matrices to consider non-agricultural LUs
        lumap[non_ag_bools_r] = (
            non_ag_X_sol_rk[non_ag_bools_r, :].argmax(axis=1)
            + settings.NON_AGRICULTURAL_LU_BASE_CODE
        )
        lmmap[non_ag_bools_r] = 0  # Assume that all non-agricultural land uses are dryland

        # Process agricultural management usage info

        # Make ammaps (agricultural management maps) using the lumap and lmmap. There is a
        # separate ammap for each agricultural management option, because they can be stacked.
        ammaps = {
            am: np.zeros(self._input_data.ncells, dtype=np.int8)
            for am in AG_MANAGEMENTS
        }
        for r in range(self._input_data.ncells):
            cell_j = lumap[r]
            cell_m = lmmap[r]

            if cell_j >= settings.NON_AGRICULTURAL_LU_BASE_CODE:
                # Non agricultural land use - no agricultural management option
                continue

            for am in self._input_data.j2am[cell_j]:
                if cell_m == 0:
                    am_var_val = am_X_dry_sol_rj[am][r, cell_j]
                else:
                    am_var_val = am_X_irr_sol_rj[am][r, cell_j]

                if am_var_val >= settings.AGRICULTURAL_MANAGEMENT_USE_THRESHOLD:
                    ammaps[am][r] = 1

        # Process production amount for each commodity
        prod_data["Production"] = (
            [
                self.total_q_exprs_c[c].getValue() * self._input_data.scale_factors['Demand']
                for c in range(self._input_data.ncms)
            ]
            if self.total_q_exprs_c 
            else 0
        ) 
        prod_data["GHG"] = (
            self.ghg_expr.getValue() * self._input_data.scale_factors['GHG']
            if self.ghg_expr
            else 0
        )
        prod_data["Water"] = (
            {
                k: v.getValue() * self._input_data.scale_factors['Water'] 
                for k,v in self.water_nyiled_exprs.items()
            }                    
            if settings.WATER_LIMITS == "on"                      
            else 0
        )
        prod_data["BIO (GBF2) value (ha)"] = (
            0                                                                               
            if settings.BIODIVERSITY_TARGET_GBF_2 == "off"         
            else self.bio_GBF2_expr.getValue() * self._input_data.scale_factors['GBF2']       
        )
        prod_data["BIO (GBF3) value (ha)"]=(
            0                                                                               
            if settings.BIODIVERSITY_TARGET_GBF_3 == "off"         
            else {
                k: v.getValue() * self._input_data.scale_factors['GBF3'] 
                for k,v in self.bio_GBF3_exprs.items()
            }
        )
        prod_data["BIO (GBF4) SNES value (ha)"] = (
            {k: v.getValue() * self._input_data.scale_factors['GBF4_SNES'] for k,v in self.bio_GBF4_SNES_exprs.items()}                   
            if settings.BIODIVERSITY_TARGET_GBF_4_SNES == "on"     
            else 0
        )
        prod_data["BIO (GBF4) ECNES value (ha)"] = (
            {k: v.getValue() * self._input_data.scale_factors['GBF4_ECNES'] for k,v in self.bio_GBF4_ECNES_exprs.items()}                  
            if settings.BIODIVERSITY_TARGET_GBF_4_ECNES == "on"    
            else 0
        )
        prod_data["BIO (GBF8) value (ha)"] = (
            {k: v.getValue() * self._input_data.scale_factors['GBF8'] for k,v in self.bio_GBF8_exprs.items()}   
            if settings.BIODIVERSITY_TARGET_GBF_8 == "on"          
            else 0
        )
                

        return SolverSolution(
            lumap=lumap,
            lmmap=lmmap,
            ammaps=ammaps,
            ag_X_mrj=ag_X_mrj_processed,
            non_ag_X_rk=non_ag_X_sol_rk,
            ag_man_X_mrj=ag_man_X_mrj_processed,
            prod_data=prod_data,
            obj_val={
                "ObjVal":(
                    None 
                    if self.gurobi_model.Status != GRB.OPTIMAL 
                    else self.gurobi_model.ObjVal
                ),
                
                "Obj Economy":                      self.obj_economy.getValue() * settings.SOLVE_WEIGHT_ALPHA,
                "Obj Biodiversity":                 self.obj_biodiv.getValue() * (1 - settings.SOLVE_WEIGHT_ALPHA),
                "Obj Penalties":                    self.obj_penalties.getValue() * settings.SOLVE_WEIGHT_BETA,
                
                'Economy (AUD) Ag':                 self.economy_ag_contr.getValue() * self._input_data.scale_factors['Economy'],
                'Economy (AUD) Non-Ag Value':       self.economy_non_ag_contr.getValue() * self._input_data.scale_factors['Economy'],
                'Economy (AUD) Ag-Man Value':       self.economy_ag_man_contr.getValue() * self._input_data.scale_factors['Economy'],                
                
                "Bio quality (score) Ag":           self.bio_ag_contr.getValue() * self._input_data.scale_factors['Biodiversity'],
                "Bio quality (score) Non-Ag":       self.bio_non_ag_contr.getValue() * self._input_data.scale_factors['Biodiversity'],
                "Bio quality (score) Ag-Man":       self.bio_ag_man_contr.getValue() * self._input_data.scale_factors['Biodiversity'],

                "Deviation Production (t)":[
                    prod_data["Production"][c] - self._input_data.limits['demand'][c]
                    for c in range(self._input_data.ncms)
                ],   
                "Deviation Water (ML)":(
                    [
                        prod_data["Water"][i] - water_limit
                        for i,water_limit in self._input_data.limits['water'].items()
                    ]                                                                        
                    if settings.WATER_LIMITS == "on"       
                    else 0
                ),         
                "Deviation GHG (tCO2e)":(
                    [ prod_data["GHG"] - self._input_data.limits['ghg'] ]                                                                        
                    if settings.GHG_CONSTRAINT_TYPE == "soft"              
                    else 0
                ),
                "Deviation BIO (GBF2) value (ha)":(
                    0                                                                             
                    if settings.BIODIVERSITY_TARGET_GBF_2 == "off"         
                    else [
                        prod_data["BIO (GBF2) value (ha)"] - self._input_data.limits['GBF2']
                    ]         
                ),
                "Deviation BIO (GBF3) value (ha)":(
                    0                                                                               
                    if settings.BIODIVERSITY_TARGET_GBF_3 == "off"         
                    else [
                        v - self._input_data.limits['GBF3'][k]
                        for k,v in prod_data["BIO (GBF3) value (ha)"].items()
                    ]
                ),
                "Deviation BIO (GBF4) SNES value (ha)":(
                    [
                        v - self._input_data.limits['GBF4_SNES'][k]
                        for k,v in prod_data["BIO (GBF4) SNES value (ha)"].items() 
                    ]                  
                    if settings.BIODIVERSITY_TARGET_GBF_4_SNES == "on"     
                    else 0
                ),
                "Deviation BIO (GBF4) ECNES value (ha)":(
                    [
                        v - self._input_data.limits['GBF4_ECNES'][k]
                        for k,v in prod_data["BIO (GBF4) ECNES value (ha)"].items()
                    ]
                    if settings.BIODIVERSITY_TARGET_GBF_4_ECNES == "on"    
                    else 0
                ),
                "Deviation BIO (GBF8) value (ha)":(
                    [
                        v - self._input_data.limits['GBF8'][k]
                        for k,v in prod_data["BIO (GBF8) value (ha)"].items()   
                    ]
                    if settings.BIODIVERSITY_TARGET_GBF_8 == "on"          
                    else 0
                ),
            }
        )


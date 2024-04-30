
import os
import re
import shutil
import keyword
import pandas as pd
import multiprocessing

from joblib import delayed, Parallel

from luto.tools.create_task_runs.parameters import EXCLUDE_DIRS, PARAMS_TO_EVAL, TASK_ROOT_DIR
from luto import settings





def create_settings_template(to_path:str=TASK_ROOT_DIR):

    # Save the settings template to the root task folder
    None if os.path.exists(to_path) else os.makedirs(to_path)
    
    # conda_pkgs, pip_pkgs = get_requirements()
    # with open(f'{to_path}/requirements_conda.txt', 'w') as conda_f, \
    #         open(f'{to_path}/requirements_pip.txt', 'w') as pip_f:
    #     conda_f.write(conda_pkgs)
    #     pip_f.write(pip_pkgs)
    
        
    if os.path.exists(f'{to_path}/settings_template.csv'):
        print('settings_template.csv already exists! Skip creating a new one!')
    else:
        # Get the settings from luto.settings
        with open('luto/settings.py', 'r') as file:
            lines = file.readlines()

            # Regex patterns that matches variable assignments from settings
            parameter_reg = re.compile(r"^(\s*[A-Z].*?)\s*=")
            settings_order = [match[1].strip() for line in lines if (match := parameter_reg.match(line))]

            # Reorder the settings dictionary to match the order in the settings.py file
            settings_dict = {i: getattr(settings, i) for i in dir(settings) if i.isupper()}
            settings_dict = {i: settings_dict[i] for i in settings_order if i in settings_dict}


        # Create a template for cutom settings
        settings_df = pd.DataFrame({k:[v] for k,v in settings_dict.items()}).T.reset_index()
        settings_df.columns = ['Name','Default_run']
        settings_df = settings_df.map(str)    
        settings_df.to_csv(f'{to_path}/settings_template.csv', index=False)





def create_task_runs(from_path:str=f'{TASK_ROOT_DIR}/settings_template.csv'):
     
    # Read the custom settings file
    custom_settings = pd.read_csv(from_path, index_col=0)
    custom_settings = custom_settings.replace({'TRUE': 'True', 'FALSE': 'False'})
    custom_settings.loc[PARAMS_TO_EVAL] = custom_settings.loc[PARAMS_TO_EVAL].map(eval)

    # Create a dictionary of custom settings
    custom_cols = [col for col in custom_settings.columns if col not in ['Default_run']]
    num_task = len(custom_cols)

    if not custom_cols:
        raise ValueError('No custom settings found in the settings_template.csv file!')

    cwd = os.getcwd()
    for col in custom_cols:
        # Get the settings for the run
        custom_dict = update_settings(custom_settings[col].to_dict(), num_task, col)
        # Check if the column name is valid, and report the changed settings
        check_settings_name(custom_settings, col)     
        # Create a folder for each run
        create_run_folders(col)    
        # Write the custom settings to the task folder
        write_custom_settings(f'{TASK_ROOT_DIR}/{col}', custom_dict)  
        # Submit the task
        submit_task(cwd, col)
        





    
def is_float(s):
    try:
        float(s)
        return True
    except ValueError:
        return False
 
 
 
 
def is_valid_variable_name(name):
    if name in keyword.kwlist:
        print(f"{name} is a keyword")
        return False
    if not name:  # Check if the name is not empty
        print("Column name is empty")
        return False
    if not name[0].isalpha() and name[0] != '_':  # Must start with a letter or underscore
        print(f"{name} must start with a letter or underscore")
        return False
    if all((char.isalnum() or char == '_') for char in name):
        return True
    print(f"{name} must contain only letters, numbers, or underscores")
    return False





def copy_folder_custom(source, destination, ignore_dirs=None):
    
    ignore_dirs = set() if ignore_dirs is None else set(ignore_dirs)

    jobs = []
    os.makedirs(destination, exist_ok=True)
    for item in os.listdir(source):
        
        if item in ignore_dirs: continue   

        s = os.path.join(source, item)
        d = os.path.join(destination, item)
        jobs += copy_folder_custom(s, d) if os.path.isdir(s) else [(s, d)]
        
    return jobs      



def get_requirements():
    with open('requirements.txt', 'r') as file:
        requirements = file.read().splitlines()
        
    split_idx = requirements.index('# Below can only be installed with pip')
    
    conda_pkgs = " ".join(requirements[:split_idx])
    pip_pkgs = " ".join(requirements[split_idx+1:])
    
    return conda_pkgs, pip_pkgs
    
  
  
    
def write_custom_settings(task_dir:str, settings_dict:dict):
    # Write the custom settings to the settings.py of each task
    with open(f'{task_dir}/luto/settings.py', 'w') as file, \
            open(f'{task_dir}/luto/settings_bash.py', 'w') as bash_file:
        for k, v in settings_dict.items():
            # List values need to be converted to bash arrays
            if isinstance(v, list):
                bash_file.write(f'{k}=({ " ".join([str(elem) for elem in v])})\n')
                file.write(f'{k}={v}\n')
            # Dict values need to be converted to bash variables
            elif isinstance(v, dict):
                file.write(f'{k}={v}\n')
                bash_file.write(f'# {k} is a dictionary, which is not natively supported in bash\n')
                for key, value in v.items():
                    bash_file.write(f'{k}_{key}={value}\n')
            # If the value is a number, write it as number
            elif str(v).isdigit() or is_float(v):
                file.write(f'{k}={v}\n')
                bash_file.write(f'{k}={v}\n')
            # If the value is a string, write it as a string
            elif isinstance(v, str):
                file.write(f'{k}="{v}"\n')
                bash_file.write(f'{k}="{v}"\n')
            # Write the rest as strings
            else:
                file.write(f'{k}={v}\n')
                bash_file.write(f'{k}={v}\n')  
    
    
                
                
def update_settings(settings_dict:dict, n_tasks:int, col:str):
    # The input dir for each task will point to the absolute path of the input dir
    settings_dict['INPUT_DIR'] = os.path.abspath(settings_dict['INPUT_DIR']).replace('\\','/')
    settings_dict['DATA_DIR'] = settings_dict['INPUT_DIR']

    # The THREADS setting will be set to the number of CPU cores divided by the number of tasks
    CPU_COUNT = multiprocessing.cpu_count()
    CPU_PER_TASK = CPU_COUNT // n_tasks - 2
    CPU_PER_TASK = max(CPU_PER_TASK, 1)
    CPU_PER_TASK = min(CPU_PER_TASK, int(settings_dict['THREADS']))
    
    settings_dict['THREADS'] = CPU_PER_TASK 
    settings_dict['WRITE_THREADS'] = CPU_PER_TASK
    

    # Set the memory and time based on the resolution factor
    if settings_dict['RESFACTOR'] == 1:
        MEM = "200G"
        TIME = "30-0:00:00"
    elif settings_dict['RESFACTOR'] == 2:
        MEM = "150G"
        TIME = "10-0:00:00"
    elif settings_dict['RESFACTOR'] == 3:
        MEM = "100G"
        TIME = "5-0:00:00"
    else:
        MEM = "80G"
        TIME = "2-0:00:00"
        
    # Set the job name base on the columns name
    JOB_NAME = f"LUTO_{col}"
    
    settings_dict['MEM'] = MEM
    settings_dict['TIME'] = TIME
    settings_dict['JOB_NAME'] = JOB_NAME


        
    return settings_dict





def check_settings_name(settings:dict, col:str):
    # Check if the column name is valid
    if not is_valid_variable_name(col):  
        raise ValueError(f'"{col}" is not a valid column name!')
    
    # Report the changed settings
    changed_params = 0
    for idx,_ in settings.iterrows():
        if settings.loc[idx,col] != settings.loc[idx,'Default_run']:
            changed_params = changed_params + 1
            print(f'"{col}" has changed <{idx}>: "{settings.loc[idx,"Default_run"]}" ==> "{settings.loc[idx,col]}">')

    print(f'"{col}" has no changed parameters compared to "Default_run"') if changed_params == 0 else None
    
    
    
    
def create_run_folders(col):
    # Copy codes to the each custom run folder, excluding {EXCLUDE_DIRS} directories
    from_to_files = copy_folder_custom(os.getcwd(), f'{TASK_ROOT_DIR}/{col}', EXCLUDE_DIRS)
    worker = min(settings.WRITE_THREADS, len(from_to_files))
    Parallel(n_jobs=worker)(delayed(shutil.copy2)(s, d) for s, d in from_to_files)
    # Create an output folder for the task
    os.makedirs(f'{TASK_ROOT_DIR}/{col}/output', exist_ok=True)




def submit_task(cwd:str, col:str):
    # Copy the slurm script to the task folder
    shutil.copyfile('luto/tools/create_task_runs/bash_scripts/slurm_cmd.sh', f'{TASK_ROOT_DIR}/{col}/slurm.sh')
    # Start the task if the os is linux
    if os.name == 'posix':
        os.chdir(f'{TASK_ROOT_DIR}/{col}')
        os.system('sbatch -p mem slurm.sh')
        os.chdir(cwd)    
    
    
    
    

        
        












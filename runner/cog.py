"""This module contains the functions to run the cog commands"""
from concurrent.futures import ProcessPoolExecutor
import json
import logging
import subprocess
import os, shutil
from typing import Any, Dict
import uuid
from pathlib import Path

from runner.__main__ import Runner
from runner.git import GitService
from runner.settings import settings

def job_get_dirs(
    job_id: uuid.UUID,
    dataset_name: str,
    model_name: str,
) -> tuple[str, str, str]:
    """Get directories for dataset and model"""
    base_dir = settings.results_dir + "/" +str(job_id)
    dataset_path = base_dir + "/" + dataset_name
    model_path = base_dir + "/" + model_name
    os.makedirs(dataset_path, exist_ok=True)
    os.makedirs(model_path, exist_ok=True)
    return base_dir, dataset_path, model_path


def copyfile(
        src: str,
        dst: str,
    ) -> None:
    """
    Copy a file from src to dst.

    Parameters:
    - src (str): The source file path.
    - dst (str): The destination directory path.

    Returns:
    None

    Raises:
    - Exception: If an error occurs during the copying process.
    """
    try:
        shutil.copy(src, dst)
    except Exception as e:
        raise Exception(f"Error copying file: {str(e)}")

def run(
    name: str,
    at: str,
    dataset_dir: str,
    base_dir: str,
    task_id: str,
    user_id: str,
    job_id: uuid.UUID,
    trained_model: str | None = None,
) -> subprocess.Popen[bytes]:
    # logger = logging.getLogger(__name__)
    # executor = ProcessPoolExecutor()

    run_script = build_cli_script(
        name=name,
        dataset_dir=dataset_dir,
        base_dir=base_dir,
        task_id=task_id,
        user_id=user_id,
        trained_model=trained_model,
        job_id=job_id
    )
    at = change2_local_dir(at)
    print(run_script)
    # stdout_file_path = Path(f"{base_dir}/{str(task_id)}/stdout.log").resolve()
    # process = executor.submit(
    #     run_process_with_std,
    #     run_script=run_script,
    #     stdout_file_path=stdout_file_path,
    #     at=at
    # )
    # process.add_done_callback(Runner.increment_worker_count)
    # process.
    return run_process_with_std(run_script=run_script, at=at)

def build_cli_script(
    name: str,
    dataset_dir: str,
    base_dir: str,
    task_id: str,
    user_id: str,
    job_id: uuid.UUID,
    trained_model: str | None = None,
) -> str:
    """
    Build a cog command to be executed in a subprocess.

    This function constructs a command-line interface (CLI) script for training a cog model.
    The script includes parameters for the dataset directory, base directory, result ID, API URL,
    user token, job ID, and an optional trained model path. The script also includes a mount
    command to bind the base directory to a specific target directory in the cog environment.

    Parameters:
    - name (str): The name of the cog.
    - dataset_dir (str): The directory path of the dataset.
    - base_dir (str): The base directory path.
    - task_id (str): The unique identifier for the task.
    - user_id (str): The user's authentication token.
    - job_id (uuid.UUID): The unique identifier for the job.
    - trained_model (str | None, optional): The path to the trained model. Defaults to None.

    Returns:
    str: The constructed CLI script as a string.
    """
    dataset_dir = replace_source_with_destination(dataset_dir, base_dir)
    run_script = f"cog train -n {str(job_id)} -i dataset={dataset_dir} -i task_id={task_id} -i pkg_name={name} -i user_id={user_id}"
    if trained_model is not None:
        trained_model = replace_source_with_destination(trained_model, base_dir)
        run_script += f" -i trained_model={trained_model}"
    # Mount the base directory
    local_dir = change2_local_dir(base_dir)
    run_script += f" --mount type=bind,source={local_dir},target={settings.cog_base_dir}"
    return run_script

def stream_process(process):
    go = process.poll() is None
    for line in process.stdout:
        print(line)
    return go


def run_process_with_std(run_script: str, at: str) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(run_script, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=at, executable="/bin/bash")
    return process

def fetch_results(at: str) -> Any:
    error = None
    success = None
    at = change2_local_dir(at)
    try:
        with open(f"{at}/success/results.json", "r") as f:
            success = json.load(f)
        return success
    except FileNotFoundError:
        try:
            with open(f"{at}/error/results.json", "r") as f:
                error = json.load(f)
            return error
        except FileNotFoundError:
            return None

async def setup(
        job_id: uuid.UUID,
        dataset_name: str,
        model_name: str,
        dataset_branch: str | None = None,
        model_branch: str | None = None,
    ) -> bool:
    """
    Setup the environment for the job.

    This function clones the dataset and model repositories to a temporary directory,
    discarding them after use. It also handles any exceptions that may occur during the cloning process.

    Parameters:
    - job_id (uuid.UUID): The unique identifier for the job.
    - dataset_name (str): The name of the dataset repository.
    - model_name (str): The name of the model repository.
    - dataset_branch (str | None, optional): The branch of the dataset repository to clone. Defaults to None.
    - model_branch (str | None, optional): The branch of the model repository to clone. Defaults to None.

    Returns:
    - bool: True if the setup is successful, False otherwise.

    Raises:
    - HTTPException: If an error occurs during the setup process.
    """
    # Clone Dataset to job_results_dir
    git = GitService()

    # clone dataset and model to a tmp directory and discard after use
    _, dataset_path, model_path = job_get_dirs(job_id, dataset_name, model_name)
    # clone specific jobb.repo_hash branch
    try:
        git.clone_repo(repo_name_with_namspace=dataset_name, to=dataset_path, branch=dataset_branch)
        git.clone_repo(repo_name_with_namspace=model_name, to=model_path, branch=model_branch)
    except Exception as e:
        remove(job_id, dataset_name, model_name)
        raise Exception(f"Error Setting up Docker Environment: {str(e)}")

    return True

async def prepare(
    job_id: uuid.UUID,
    dataset_name: str,
    model_name: str,
    dataset_type: str,
    results_dir: str = "",
    dataset_branch: str | None = None,
    model_branch: str | None = None,
) -> bool:
    """
    Prepare the environment for the job.

    If the dataset type is 'upload', it copies the
    dataset from the results directory to the dataset path. If the dataset type is 'default',
    it fetches the dataset from the specified branch of the dataset repository.
    It also fetches the model from the specified branch of the model repository.

    Parameters:
    - job_id (uuid.UUID): The unique identifier for the job.
    - dataset_name (str): The name of the dataset repository or the path to the dataset
    - model_name (str): The name of the model repository.
    - dataset_type (str): The type of the dataset. It can be either 'upload' or 'default'.
    - results_dir (str, optional): The directory path where the uploaded dataset is located. Defaults to an empty string.
    - dataset_branch (str | None, optional): The branch of the dataset repository to clone. Defaults to None.
    - model_branch (str | None, optional): The branch of the model repository to clone. Defaults to None.

    Returns:
    - bool: True if the preparation is successful, False otherwise.

    Raises:
    - HTTPException: If an error occurs during the preparation process.
    """
    # Clone Dataset to job_results_dir
    git = GitService()
    _, dataset_path, model_path = job_get_dirs(job_id, dataset_name, model_name)

    try:
        # run git
        if dataset_type == 'upload':
            copyfile(dataset_name,results_dir)
        elif dataset_type == 'default':
            git.fetch(repo_name_with_namspace=dataset_name, to=dataset_path, branch= dataset_branch)
        git.fetch(repo_name_with_namspace=model_name, to=model_path, branch= model_branch)
    except Exception as e:
        raise Exception(f"Error Preparing Docker Environment: {str(e)}")

    return True

def stop(job_id: uuid.UUID) -> bool:
    """
    Stop the jobs for container.

    This function stops and removes all Docker containers that have the specified job_id as their ancestor.
    It uses the Docker CLI commands 'docker ps -a -q  --filter ancestor={str(job_id)}' to get the list of container IDs,
    and then iterates over these IDs to stop and remove each container.

    Parameters:
    - job_id (uuid.UUID): The unique identifier for the job.

    Returns:
    - bool: True if all containers are successfully stopped and removed, False otherwise.

    Raises:
    - None

    Note:
    - This function uses the os.system() function to execute Docker CLI commands.
    """
    # get the results from running this command f"docker ps -a -q  --filter ancestor={str(job_id)}"
    process = subprocess.run(f"docker ps -a -q  --filter ancestor={str(job_id)}", shell=True, stdout=subprocess.PIPE, executable="/bin/bash", check=False)
    if process.returncode!= 0:
        return False
    # get results from stdout
    results = process.stdout.decode("utf-8").split("\n")
    for result in results:
        if result == "":
            continue
        os.system(f"docker stop {result}")
        os.system(f"docker rm {result}")
    return True

def remove(job_id: uuid.UUID, dataset_name: str, model_name: str) -> bool:
    """
    Remove the environment for the job.

    This function removes the dataset and model directories associated with the given job_id.
    It uses the os.system() function to execute the 'rm -rf' command to delete the directories.

    Parameters:
    - job_id (uuid.UUID): The unique identifier for the job.
    - dataset_name (str): The name of the dataset repository.
    - model_name (str): The name of the model repository.

    Returns:
    - bool: True if the directories are successfully removed, False otherwise.

    Note:
    - This function uses the os.system() function to execute the 'rm -rf' command, which can be a security risk.
    - It is recommended to use a safer alternative, such as shutil.rmtree(), for removing directories in production code.
    """
    _, dataset_path, model_path = job_get_dirs(job_id, dataset_name, model_name)
    os.system(f"rm -rf {dataset_path}")  # noqa: F821
    os.system(f"rm -rf {model_path}")
    return True

def remove_docker(job_id: uuid.UUID) -> None:
    """
    Remove docker image which serves as env from machine.

    This function uses the os.system() function to execute the 'docker rmi' command,
    which removes a Docker image from the local machine. The image to be removed is
    identified by its unique job_id.

    Parameters:
    - job_id (uuid.UUID): The unique identifier for the job. This is used to identify the Docker image to be removed.

    Returns:
    - None: This function does not return any value.

    Note:
    - This function uses the os.system() function to execute the 'docker rmi' command, which can be a security risk.
    - It is recommended to use a safer alternative, such as Docker SDK for Python, for interacting with Docker in production code.
    """
    os.system(f"docker rmi {str(job_id)}")

def replace_source_with_destination(at: str, base_dir: str) -> str:
    """
    Replace the source directory with the destination directory.

    This function is used to replace the source directory path with the destination directory path.
    It is used in the context of setting up a cog environment, where the source directory is replaced
    with the destination directory in the command-line script.

    Parameters:
    - at (str): The original source directory path.
    - base_dir (str): The destination directory path.

    Returns:
    str: The updated command-line script with the source directory replaced by the destination directory.

    Note:
    - This function is used in the context of setting up a cog environment.
    - The source directory is replaced with the destination directory in the command-line script.
    """
    return at.replace(base_dir, settings.cog_base_dir)

def change2_local_dir(base_dir: str) -> str:
    return base_dir.replace(settings.server_base_dir, settings.results_dir)
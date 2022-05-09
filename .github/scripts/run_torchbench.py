"""
Generate a torchbench test report from a file containing the PR body.
Currently, only supports running tests on specified model names

Testing environment:
- Intel Xeon 8259CL @ 2.50 GHz, 24 Cores with disabled Turbo and HT
- Nvidia Tesla T4
- Nvidia Driver 450.51.06
- Python 3.7
- CUDA 10.2
"""
# Known issues:
# 1. Does not reuse the build artifact in other CI workflows
# 2. CI jobs are serialized because there is only one worker
import os
import git  # type: ignore[import]
import pathlib
import argparse
import subprocess

from typing import List

# This is a test comment

TORCHBENCH_CONFIG_NAME = "config.yaml"
MAGIC_PREFIX = "RUN_TORCHBENCH:"
MAGIC_TORCHBENCH_PREFIX = "TORCHBENCH_BRANCH:"
ABTEST_CONFIG_TEMPLATE = """# This config is automatically generated by run_torchbench.py
start: {control}
end: {treatment}
threshold: 100
direction: decrease
timeout: 720
tests:"""

def gen_abtest_config(control: str, treatment: str, models: List[str]) -> str:
    d = {}
    d["control"] = control
    d["treatment"] = treatment
    config = ABTEST_CONFIG_TEMPLATE.format(**d)
    if models == ["ALL"]:
        return config + "\n"
    for model in models:
        config = f"{config}\n  - {model}"
    config = config + "\n"
    return config

def setup_gha_env(name: str, val: str) -> None:
    fname = os.environ["GITHUB_ENV"]
    content = f"{name}={val}\n"
    with open(fname, "a") as fo:
        fo.write(content)

def find_current_branch(repo_path: str) -> str:
    repo = git.Repo(repo_path)
    name: str = repo.active_branch.name
    return name

def deploy_torchbench_config(output_dir: str, config: str) -> None:
    # Create test dir if needed
    pathlib.Path(output_dir).mkdir(exist_ok=True)
    # TorchBench config file name
    config_path = os.path.join(output_dir, TORCHBENCH_CONFIG_NAME)
    with open(config_path, "w") as fp:
        fp.write(config)

def extract_models_from_pr(torchbench_path: str, prbody_file: str) -> List[str]:
    model_list = []
    with open(prbody_file, "r") as pf:
        lines = map(lambda x: x.strip(), pf.read().splitlines())
        magic_lines = list(filter(lambda x: x.startswith(MAGIC_PREFIX), lines))
        if magic_lines:
            # Only the first magic line will be recognized.
            model_list = list(map(lambda x: x.strip(), magic_lines[0][len(MAGIC_PREFIX):].split(",")))
    # Shortcut: if model_list is ["ALL"], run all the tests
    if model_list == ["ALL"]:
        return model_list
    # Sanity check: make sure all the user specified models exist in torchbench repository
    benchmark_path = os.path.join(torchbench_path, "torchbenchmark", "models")
    full_model_list = [model for model in os.listdir(benchmark_path) if os.path.isdir(os.path.join(benchmark_path, model))]
    for m in model_list:
        if m not in full_model_list:
            print(f"The model {m} you specified does not exist in TorchBench suite. Please double check.")
            return []
    return model_list

def find_torchbench_branch(prbody_file: str) -> str:
    branch_name: str = ""
    with open(prbody_file, "r") as pf:
        lines = map(lambda x: x.strip(), pf.read().splitlines())
        magic_lines = list(filter(lambda x: x.startswith(MAGIC_TORCHBENCH_PREFIX), lines))
        if magic_lines:
            # Only the first magic line will be recognized.
            branch_name = magic_lines[0][len(MAGIC_TORCHBENCH_PREFIX):].strip()
    # If not specified, use main as the default branch
    if not branch_name:
        branch_name = "main"
    return branch_name

def run_torchbench(pytorch_path: str, torchbench_path: str, output_dir: str) -> None:
    # Copy system environment so that we will not override
    env = dict(os.environ)
    command = ["python", "bisection.py", "--work-dir", output_dir,
               "--pytorch-src", pytorch_path, "--torchbench-src", torchbench_path,
               "--config", os.path.join(output_dir, "config.yaml"),
               "--output", os.path.join(output_dir, "result.txt")]
    subprocess.check_call(command, cwd=torchbench_path, env=env)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run TorchBench tests based on PR')
    parser.add_argument('--pr-body', required=True, help="The file that contains body of a Pull Request")

    subparsers = parser.add_subparsers(dest='command')
    # parser for setup the torchbench branch name env
    branch_parser = subparsers.add_parser("set-torchbench-branch")
    # parser to run the torchbench branch
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument('--pr-num', required=True, type=str, help="The Pull Request number")
    run_parser.add_argument('--pr-base-sha', required=True, type=str, help="The Pull Request base hash")
    run_parser.add_argument('--pr-head-sha', required=True, type=str, help="The Pull Request head hash")
    run_parser.add_argument('--pytorch-path', required=True, type=str, help="Path to pytorch repository")
    run_parser.add_argument('--torchbench-path', required=True, type=str, help="Path to TorchBench repository")
    args = parser.parse_args()

    if args.command == 'set-torchbench-branch':
        branch_name = find_torchbench_branch(args.pr_body)
        # env name: "TORCHBENCH_BRANCH"
        setup_gha_env(MAGIC_TORCHBENCH_PREFIX[:-1], branch_name)
    elif args.command == 'run':
        output_dir: str = os.path.join(os.environ["HOME"], ".torchbench", "bisection", f"pr{args.pr_num}")
        # Identify the specified models and verify the input
        models = extract_models_from_pr(args.torchbench_path, args.pr_body)
        if not models:
            print("Can't parse the model filter from the pr body. Currently we only support allow-list.")
            exit(-1)
        # Assert the current branch in args.torchbench_path is the same as the one specified in pr body
        branch_name = find_torchbench_branch(args.pr_body)
        current_branch = find_current_branch(args.torchbench_path)
        assert branch_name == current_branch, f"Torchbench repo {args.torchbench_path} is on branch {current_branch}, \
                                                but user specified to run on branch {branch_name}."
        print(f"Ready to run TorchBench with benchmark. Result will be saved in the directory: {output_dir}.")
        # Run TorchBench with the generated config
        torchbench_config = gen_abtest_config(args.pr_base_sha, args.pr_head_sha, models)
        deploy_torchbench_config(output_dir, torchbench_config)
        run_torchbench(pytorch_path=args.pytorch_path, torchbench_path=args.torchbench_path, output_dir=output_dir)
    else:
        print(f"The command {args.command} is not supported.")
        exit(-1)

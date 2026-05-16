import os
import subprocess

# Configuration
REPO_URL = "https://github.com/123Valentine/your-repo.git"  # replace with your actual repo URL
BATCH_SIZE = 20
MAX_FILE_SIZE_MB = 100
IGNORE_FOLDERS = ['venv', '.env', '__pycache__']

def is_ignored(path):
    for folder in IGNORE_FOLDERS:
        if folder in path.split(os.sep):
            return True
    return False

def get_files_to_add(root_dir="."):
    files = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        for f in filenames:
            full_path = os.path.join(dirpath, f)
            if is_ignored(full_path):
                continue
            if os.path.getsize(full_path) > MAX_FILE_SIZE_MB * 1024 * 1024:
                continue
            files.append(full_path)
    return files

def run_command(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running {' '.join(cmd)}: {result.stderr}")
    return result

def push_batches(files):
    for i in range(0, len(files), BATCH_SIZE):
        batch = files[i:i+BATCH_SIZE]
        run_command(["git", "add"] + batch)
        commit_message = f"Auto commit batch {i//BATCH_SIZE + 1}"
        run_command(["git", "commit", "-m", commit_message])
        run_command(["git", "push", "-u", "origin", "main"])
        print(f"Done batch {i//BATCH_SIZE + 1}")

def main():
    # Initialize git if not present
    if not os.path.exists(".git"):
        print("Initializing git repository...")
        run_command(["git", "init"])
        run_command(["git", "checkout", "-b", "main"])
        run_command(["git", "remote", "add", "origin", REPO_URL])
    
    # Make sure main branch exists
    run_command(["git", "checkout", "-b", "main"])  # Will do nothing if branch exists

    files = get_files_to_add()
    if files:
        push_batches(files)
    else:
        print("No files to push.")

if __name__ == "__main__":
    main()
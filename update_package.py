import os
import subprocess
import sys
import re
from github import Github
from getpass import getpass
from github.GithubException import GithubException
import keyring
from twine.commands.upload import upload
from twine.settings import Settings
from packaging import version

def run_command(command):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    output, error = process.communicate()
    if process.returncode != 0:
        print(f"Error: {error.decode('utf-8')}")
        return False
    return output.decode('utf-8').strip()

def get_or_set_credential(service, credential_type, prompt):
    credential = keyring.get_password(service, credential_type)
    if not credential:
        credential = input(prompt) if credential_type == 'username' else getpass(prompt)
        save = input(f"Do you want to save the {service} {credential_type}? (y/n): ").lower().strip() == 'y'
        if save:
            keyring.set_password(service, credential_type, credential)
    return credential

def github_login(max_attempts=3):
    for attempt in range(max_attempts):
        token = os.environ.get('GITHUB_TOKEN') or get_or_set_credential("github", "token", "Enter your GitHub personal access token: ")
        try:
            g = Github(token)
            user = g.get_user()
            print(f"Logged in as: {user.login}")
            return g
        except GithubException:
            print("GitHub login failed. Token might be expired or invalid.")
            keyring.delete_password("github", "token")
            if attempt < max_attempts - 1:
                print("Please try again.")
            else:
                print("Max attempts reached. Exiting.")
                sys.exit(1)

def ensure_github_remote(github):
    remotes = run_command('git remote -v')
    if remotes is False:
        print("Failed to get git remotes. Please check your git installation.")
        sys.exit(1)
    
    if 'origin' not in remotes:
        repo_name = input("Enter the GitHub repository name: ")
        user = github.get_user()
        repo_url = f"https://github.com/{user.login}/{repo_name}.git"
        if run_command(f'git remote add origin {repo_url}') is False:
            print("Failed to add remote. Please check your git configuration.")
            sys.exit(1)
    
    remote_url = run_command('git remote get-url origin')
    if remote_url is False:
        print("Failed to get remote URL. Please check your git configuration.")
        sys.exit(1)
    
    repo_name = remote_url.split('/')[-1].replace('.git', '')
    print(f"GitHub remote 'origin' is set up for repository: {repo_name}")
    return repo_name

def sync_with_remote():
    print("Syncing with remote repository...")
    if run_command('git fetch origin') is False or run_command('git merge origin/main --allow-unrelated-histories --no-edit') is False:
        print("There might be merge conflicts. Please resolve them manually and run the script again.")
        sys.exit(1)

def push_to_remote():
    print("Pushing to GitHub...")
    if run_command('git push -u origin main') is False:
        print("Push failed. Please check your repository and try manually.")
        return False
    return True

def upload_to_pypi(dist_files, max_attempts=3):
    for attempt in range(max_attempts):
        username = os.environ.get('PYPI_USERNAME') or get_or_set_credential("pypi", "username", "Enter your PyPI username: ")
        password = os.environ.get('PYPI_PASSWORD') or get_or_set_credential("pypi", "token", "Enter your PyPI token: ")

        settings = Settings(
            username=username,
            password=password,
            repository_url='https://upload.pypi.org/legacy/'
        )

        try:
            upload(settings, dist_files)
            print("Successfully uploaded to PyPI")
            return True
        except Exception as e:
            print(f"Failed to upload to PyPI: {str(e)}")
            keyring.delete_password("pypi", "username")
            keyring.delete_password("pypi", "token")
            if attempt < max_attempts - 1:
                print("Please try again.")
            else:
                print("Max attempts reached. Please check your PyPI credentials and try manually.")
                return False

def update_version():
    with open('VERSION', 'r') as f:
        current_version = f.read().strip()
    
    print(f"Current version: {current_version}")
    while True:
        new_version = input("Enter new version number (e.g., 0.1.7): ")
        if re.match(r'^\d+(\.\d+){0,2}(\.?[a-zA-Z0-9]+)?$', new_version):
            try:
                version.parse(new_version)
                break
            except version.InvalidVersion:
                print("Invalid version format. Please use a valid version number.")
        else:
            print("Invalid version format. Please use a valid version number.")
    
    # Update VERSION file
    with open('VERSION', 'w') as f:
        f.write(new_version)
    
    # Update setup.py
    with open('setup.py', 'r') as f:
        content = f.read()
    
    updated_content = re.sub(r"version=['\"][\d.]+['\"]", f"version='{new_version}'", content)
    
    with open('setup.py', 'w') as f:
        f.write(updated_content)
    
    print(f"Updated VERSION file and setup.py with new version: {new_version}")
    return new_version

def main():
    if not os.path.exists('.git'):
        print("Error: Not in a git repository. Please run this script from your project's root directory.")
        sys.exit(1)

    github = github_login()
    repo_name = ensure_github_remote(github)
    sync_with_remote()
    new_version = update_version()
    change_description = input("Enter a brief description of the changes: ")

    if run_command('git add .') is False or run_command(f'git commit -m "Update to version {new_version}: {change_description}"') is False:
        print("Failed to commit changes. Please check your git configuration.")
        sys.exit(1)

    if push_to_remote() is False:
        print("Failed to push to GitHub. Aborting.")
        sys.exit(1)

    user = github.get_user()
    repo = user.get_repo(repo_name)
    try:
        repo.create_git_release(f"v{new_version}", f"Version {new_version}", change_description)
        print(f"GitHub release created for version {new_version}")
    except GithubException as e:
        print(f"An error occurred while creating the GitHub release: {str(e)}")
        print("Please create the release manually on the GitHub website.")
        sys.exit(1)

    print("Cleaning old distribution files...")
    if os.path.exists('dist'):
        for file in os.listdir('dist'):
            os.remove(os.path.join('dist', file))

    print("Building distribution...")
    if run_command('python setup.py sdist bdist_wheel') is False:
        print("Failed to build distribution. Please check your setup.py and try manually.")
        sys.exit(1)

    print("Uploading to PyPI...")
    dist_files = [f for f in os.listdir('dist') if f.endswith(('.whl', '.tar.gz'))]
    dist_files = [os.path.join('dist', f) for f in dist_files]
    if upload_to_pypi(dist_files):
        print(f"Package updated to version {new_version} and pushed to GitHub and PyPI")
        # Update setup.py with the new version
        update_setup_py_version(new_version)
    else:
        print("Failed to upload to PyPI. Version in setup.py not updated.")
        sys.exit(1)

def update_setup_py_version(new_version):
    with open('setup.py', 'r') as f:
        content = f.read()
    
    updated_content = re.sub(r"version='[\d.]+'", f"version='{new_version}'", content)
    
    with open('setup.py', 'w') as f:
        f.write(updated_content)
    
    print(f"Updated setup.py with new version: {new_version}")

if __name__ == "__main__":
    main()
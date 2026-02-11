# CI/CD Pipeline Documentation

This document describes the comprehensive CI/CD pipeline setup for the Datavia project.

## Pipeline Overview

We have three separate workflows to handle different stages of the development lifecycle:

### 1. 🔍 CI Pipeline (`ci-pull-request.yml`)
**Triggered on**: Pull Requests to `main`

**Purpose**: Ensure code quality and functionality before merging

**Features**:
- **Auto-formatting**: Automatically applies `black` and `isort` formatting and commits back to the PR
- **Code quality**: Linting with `ruff`, type checking with `mypy`
- **Testing**: Comprehensive test suite across Python 3.9-3.12
- **Security**: Vulnerability scanning with `bandit` and `safety`
- **Documentation**: Validates docs build correctly
- **Build verification**: Ensures packages can be built

### 2. 🚀 CD Pipeline (`cd-main.yml`)
**Triggered on**: Pushes to `main` branch

**Purpose**: Deploy development versions and update documentation

**Features**:
- **Documentation deployment**: Automatically deploys docs to GitHub Pages
- **Development releases**: Creates development releases with auto-generated version numbers
- **Staging deployment**: (Placeholder for staging environment deployment)
- **Team notifications**: Notifies team of successful/failed deployments

### 3. 📦 Release Pipeline (`release-pypi.yml`)
**Triggered on**: Pushes to `release` branch or version tags (`v*.*.*`)

**Purpose**: Create official releases and publish to PyPI

**Features**:
- **Comprehensive testing**: Full test suite across all supported Python versions
- **Quality gate**: All linting, formatting, and security checks must pass
- **Version management**: Automatic version handling and tagging
- **PyPI publishing**: Publishes to TestPyPI first, then PyPI
- **GitHub releases**: Creates release notes with changelog
- **Post-release tasks**: Merges back to main and bumps dev version

## Workflow

### For Developers (Pull Requests)

1. **Create feature branch** from `main`
2. **Make changes** and push to your branch
3. **Create Pull Request** targeting `main`
4. **CI Pipeline runs automatically**:
   - Formats your code automatically (no manual fixing needed!)
   - Runs all tests and quality checks
   - Provides feedback on any issues
5. **Merge when CI passes**

### For Maintainers (Releases)

#### Development Releases (Automatic)
- Every merge to `main` automatically creates a development release
- Version format: `1.0.0-dev.20240203.abcdef0`
- Available as GitHub release (prerelease)

#### Official Releases (Manual)
1. **Prepare release**:
   ```bash
   # Update version (removes -dev suffix)
   ./scripts/version.sh patch  # or minor/major
   
   # Push to release branch
   git checkout -b release
   git push origin release
   ```

2. **Release pipeline runs automatically**:
   - Runs comprehensive tests
   - Publishes to TestPyPI first
   - Publishes to PyPI
   - Creates GitHub release
   - Merges back to main

## Auto-Formatting Strategy

**Problem**: Manual formatting fixes are time-consuming and error-prone.

**Solution**: The CI pipeline automatically fixes formatting issues and commits them back to your PR.

### How it works:
1. When you create a PR, CI checks formatting with `black` and `isort`
2. If formatting issues are found, CI automatically:
   - Applies the fixes
   - Commits the changes with message "Auto-fix: Apply code formatting [skip ci]"
   - Pushes back to your PR branch
3. Your PR is automatically updated with properly formatted code

### Benefits:
- ✅ No manual formatting needed
- ✅ Consistent code style across the project
- ✅ Saves developer time
- ✅ No more "fix formatting" review comments

## Setup Requirements

### Repository Secrets
Add these secrets in your GitHub repository settings:

1. **PYPI_API_TOKEN**: Your PyPI API token for publishing releases
   - Get from: https://pypi.org/manage/account/token/
   
2. **TEST_PYPI_API_TOKEN**: TestPyPI token (optional, for testing)
   - Get from: https://test.pypi.org/manage/account/token/

### Branch Protection
Configure branch protection for `main`:
- Require status checks to pass before merging
- Require branches to be up to date before merging
- Require review from code owners

## Version Management

Use the included version script for easy version management:

```bash
# Show current version
./scripts/version.sh

# Increment versions
./scripts/version.sh patch    # 1.0.0 -> 1.0.1
./scripts/version.sh minor    # 1.0.0 -> 1.1.0  
./scripts/version.sh major    # 1.0.0 -> 2.0.0

# Set specific version
./scripts/version.sh set 1.2.3

# Add development suffix
./scripts/version.sh dev      # 1.0.0 -> 1.0.0-dev
```

## Monitoring and Debugging

### Viewing Pipeline Status
- **GitHub Actions tab**: See all workflow runs
- **PR checks**: Status checks appear on pull requests
- **Release page**: View all releases and their assets

### Common Issues

1. **Formatting auto-fix not working**:
   - Check that the bot has write permissions
   - Verify `GITHUB_TOKEN` has necessary permissions

2. **PyPI publish failing**:
   - Verify `PYPI_API_TOKEN` is correct
   - Check that version number isn't already published
   - Ensure package builds successfully

3. **Tests failing on specific Python version**:
   - Check compatibility in your dependencies
   - Review Python version-specific code

### Logs and Artifacts
- Test results and coverage reports are uploaded as artifacts
- Security scan results available for download
- Build packages stored as artifacts for debugging

## Customization

### Adding New Tests
Add test files to the `tests/` directory. They will be automatically discovered and run.

### Modifying Quality Checks
Edit the quality jobs in `ci-pull-request.yml` to add/remove linters or change configurations.

### Adding Deployment Targets
Modify the CD pipeline to add deployment to staging/production environments.

### Notification Integration
Add Slack, Discord, or email notifications to the workflows for team updates.

## Best Practices

1. **Keep PRs focused**: Smaller PRs are easier to review and test
2. **Write good commit messages**: They appear in release changelogs
3. **Update tests**: Add tests for new features
4. **Monitor CI feedback**: Address any quality issues early
5. **Use draft PRs**: For work-in-progress to avoid triggering full CI

## Support

If you need help with the CI/CD pipeline:
1. Check the workflow logs in the GitHub Actions tab
2. Review this documentation
3. Ask in the team chat or create an issue

---

*This CI/CD setup is designed to make your development workflow smooth and automated. Focus on writing great code - we'll handle the rest!* 🚀
# Release Preparation Guide

## ✅ Completed

- [x] Created `MANIFEST.in` for package distribution
- [x] Created `.github/workflows/publish.yml` for automated PyPI publishing
- [x] Existing `.github/workflows/test.yml` runs tests on Python 3.11, 3.12, 3.13
- [x] Version set to 0.1.0 in `pyproject.toml`
- [x] CHANGELOG.md ready with v0.1.0 release notes
- [x] LICENSE (Apache 2.0) and NOTICE files present
- [x] `py.typed` marker for type annotations

## 🔧 Required Setup Steps

### 1. PyPI Account Setup

1. **Create PyPI accounts:**
   - Production: https://pypi.org/account/register/
   - Testing: https://test.pypi.org/account/register/

2. **Generate API tokens:**
   - PyPI: Account Settings → API tokens → "Add API token"
     - Token name: `mfp-github-actions`
     - Scope: "Entire account" (or project-specific after first upload)
   - TestPyPI: Repeat above steps

3. **Add tokens to GitHub Secrets:**
   - Go to: `https://github.com/Madahub-dev/MFP/settings/secrets/actions`
   - Add repository secrets:
     - Name: `PYPI_API_TOKEN`, Value: `<your-pypi-token>`
     - Name: `TEST_PYPI_API_TOKEN`, Value: `<your-testpypi-token>`

   **Note:** The current workflow uses Trusted Publishers (OIDC), which is more secure. To use this:
   - Go to PyPI → Publishing → Add a new publisher
   - Owner: `Madahub-dev`
   - Repository: `MFP`
   - Workflow: `publish.yml`
   - Environment: `pypi` (or `testpypi`)

### 2. GitHub Branch Protection

Protect the `main` branch:

1. Go to: `https://github.com/Madahub-dev/MFP/settings/branches`
2. Click "Add rule" or "Add branch protection rule"
3. Branch name pattern: `main`
4. Enable:
   - ✅ Require a pull request before merging
     - ✅ Require approvals: 1
     - ✅ Dismiss stale pull request approvals when new commits are pushed
   - ✅ Require status checks to pass before merging
     - ✅ Require branches to be up to date before merging
     - Search for and add: `test (3.11)`, `test (3.12)`, `test (3.13)`, `lint`
   - ✅ Require conversation resolution before merging
   - ✅ Do not allow bypassing the above settings
5. Click "Create" or "Save changes"

### 3. GitHub Environments (for Trusted Publishing)

Create deployment environments:

1. Go to: `https://github.com/Madahub-dev/MFP/settings/environments`
2. Create `pypi` environment:
   - Click "New environment"
   - Name: `pypi`
   - Add protection rules (optional but recommended):
     - Required reviewers: Add yourself
     - Wait timer: 0 minutes
3. Create `testpypi` environment (repeat above with name `testpypi`)

### 4. Test the Build Locally

Before releasing, verify the package builds correctly:

```bash
# Install build tools
pip install build twine

# Build the package
python -m build

# Check the distribution
twine check dist/*

# Inspect the contents
tar -tzf dist/mfp-0.1.0.tar.gz
```

Expected outputs:
- `dist/mfp-0.1.0.tar.gz` (source distribution)
- `dist/mfp-0.1.0-py3-none-any.whl` (wheel distribution)

### 5. Test Upload to TestPyPI

Before publishing to production PyPI, test with TestPyPI:

**Option A: Using the workflow (recommended)**
1. Go to: `https://github.com/Madahub-dev/MFP/actions/workflows/publish.yml`
2. Click "Run workflow"
3. Select branch: `main`
4. Environment: `testpypi`
5. Click "Run workflow"

**Option B: Manual upload**
```bash
# Upload to TestPyPI
twine upload --repository testpypi dist/*

# Test installation
pip install --index-url https://test.pypi.org/simple/ --no-deps mfp
```

### 6. Create the First Release

When ready for v0.1.0 release:

1. **Ensure all changes are committed:**
   ```bash
   git add .
   git commit -m "Prepare for v0.1.0 release"
   git push origin main
   ```

2. **Create a Git tag:**
   ```bash
   git tag -a v0.1.0 -m "Release v0.1.0 - Initial alpha release"
   git push origin v0.1.0
   ```

3. **Create GitHub Release:**
   - Go to: `https://github.com/Madahub-dev/MFP/releases/new`
   - Tag: `v0.1.0` (select the tag you just created)
   - Release title: `MFP v0.1.0`
   - Description: Copy from CHANGELOG.md (lines 42-112)
   - Click "Publish release"

4. **Automated PyPI upload:**
   - The `publish.yml` workflow will automatically trigger
   - Monitor at: `https://github.com/Madahub-dev/MFP/actions`
   - Package will be published to: `https://pypi.org/project/mfp/`

## 📋 Pre-Release Checklist

Before creating v0.1.0 release:

- [ ] All tests passing in CI
- [ ] CHANGELOG.md is up to date
- [ ] README.md accurately reflects features
- [ ] Version in pyproject.toml is correct (0.1.0)
- [ ] LICENSE and NOTICE files are correct
- [ ] Documentation is complete
- [ ] No uncommitted changes in git
- [ ] Package builds successfully locally
- [ ] Test upload to TestPyPI successful
- [ ] Branch protection is enabled
- [ ] PyPI/TestPyPI accounts and tokens configured

## 🚀 Post-Release

After successful release:

1. **Verify PyPI upload:**
   ```bash
   pip install mfp
   python -c "import mfp; print(mfp.__version__)"
   ```

2. **Update README badge (optional):**
   Add to README.md:
   ```markdown
   [![PyPI version](https://badge.fury.io/py/mfp.svg)](https://pypi.org/project/mfp/)
   [![Python versions](https://img.shields.io/pypi/pyversions/mfp.svg)](https://pypi.org/project/mfp/)
   ```

3. **Announce the release:**
   - Social media
   - madahub.org
   - Relevant communities

## 🔄 Future Releases

For subsequent releases:

1. Update version in `pyproject.toml`
2. Update CHANGELOG.md with new version section
3. Commit changes: `git commit -m "Bump version to X.Y.Z"`
4. Create tag: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`
5. Push: `git push origin main --tags`
6. Create GitHub Release (triggers auto-publish to PyPI)

## 📚 References

- [PyPI Publishing Guide](https://packaging.python.org/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)
- [Trusted Publishers](https://docs.pypi.org/trusted-publishers/)
- [Semantic Versioning](https://semver.org/)
- [Keep a Changelog](https://keepachangelog.com/)

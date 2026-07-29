# Version Tag Workflow

This repository contains GitHub Actions workflows

## Workflow: `create-version-based-on-tag.yaml`

### Purpose
This workflow extracts the version number from a pushed Git tag and updates `__version__` in the specified Python package's `__init__.py` accordingly.

### Location
```
.github/workflows/create-version-based-on-tag.yaml
```

## Inputs
The workflow is designed to be reusable using `workflow_call`. It accepts the following input:

| Name          | Description                                         | Required | Type   | Default          |
|--------------|-----------------------------------------------------|----------|--------|------------------|
| package_path | Path to the package directory containing `__init__.py` | ✅        | string | `my_repo_name`   |

## Permissions
The workflow requires `contents: write` permission to create and push tags.

## Usage Example

### Create Version from TAG

```yaml
name: Version Tag Creator

on:
  push:
    tags:
      - 'v*'

jobs:
  call-version-tag-workflow:
    uses: Centura-AG/centura_workflows/.github/workflows/create-version-based-on-tag.yaml
    permissions:
      contents: write
    with:
      package_path: ${{ github.event.repository.name }}
      pushed_tag: ${{ github.ref_name }}

```

## Requirements
- The target repository must contain a Python package with an `__init__.py` file where `__version__` is defined.
- The workflow should be triggered when a version tag (`v*`) is pushed.
- Ensure `contents: write` permission is granted to allow tag creation.

## License
This project is licensed under the MIT License. You are free to use, modify, and distribute it. However, it comes without any warranty or liability. Use it at your own risk.


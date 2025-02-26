# Version Tag Workflow

This repository contains GitHub Actions workflows

## Workflow: `version-tag-workflow.yaml`

### Purpose
This workflow extracts the version number from the specified Python package directory and creates a new Git tag if it does not already exist.

### Location
```
.github/workflows/version-tag-workflow.yaml
```

## Inputs
The workflow is designed to be reusable using `workflow_call`. It accepts the following input:

| Name          | Description                                         | Required | Type   | Default          |
|--------------|-----------------------------------------------------|----------|--------|------------------|
| package_path | Path to the package directory containing `__init__.py` | ✅        | string | `my_repo_name`   |

## Permissions
The workflow requires `contents: write` permission to create and push tags.

## Usage Example
This workflow can be used in another repository as follows:

```yaml
name: Version Tag Creator

on:
  push:
    paths:
      - 'my_repo/__init__.py'
    branches:
      - 'develop'

jobs:
  call-version-tag-workflow:
    uses: Centura-AG/centura_workflows/.github/workflows/version-tag-workflow.yaml@develop
    permissions:
      contents: write
    with:
      package_path: ${{ github.event.repository.name }}
```

## Requirements
- The target repository must contain a Python package with an `__init__.py` file where `__version__` is defined.
- The workflow should be triggered when a relevant change is made to the `__init__.py` file.
- Ensure `contents: write` permission is granted to allow tag creation.

## License
This project is licensed under the MIT License. You are free to use, modify, and distribute it. However, it comes without any warranty or liability. Use it at your own risk.


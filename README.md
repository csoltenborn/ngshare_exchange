# ngshare_exchange
[![codecov](https://codecov.io/gh/LibreTexts/ngshare_exchange/branch/master/graph/badge.svg)](https://codecov.io/gh/LibreTexts/ngshare_exchange)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

Custom [nbgrader](https://github.com/jupyter/nbgrader) exchange to be used with [ngshare](https://github.com/LibreTexts/ngshare). This should be installed in the singleuser image of [Z2JH](https://github.com/jupyterhub/zero-to-jupyterhub-k8s) to allow the users to use ngshare.

# Installation instructions

To install this and `ngshare`, see the instructions on [Read the Docs](https://ngshare.readthedocs.io/en/latest/user_guide/install.html).

# ngshare Course Management

Student rosters can now be managed directly in the **Formgrader** web interface ("Manage Students" page), which automatically synchronizes changes (adding, editing, and deleting students) with `ngshare` and the local gradebook.

Alternatively, you can manage students using the `ngshare-course-management` command-line utility installed with this package or via the JupyterHub Class Management UI. Please [read the documentation](https://ngshare.readthedocs.io/en/latest/user_guide/course_management.html) for command-line instructions.

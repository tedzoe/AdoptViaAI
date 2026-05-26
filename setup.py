"""
AdoptviaAI — AI adoption done right
setup.py: installs the 'avai' CLI command.

CCA-F Note: setup.py wires the 'avai' console_script entry point to
main:cli, making the tool runnable as a system-level command after
`pip install -e .`
"""

from setuptools import find_packages, setup

setup(
    name="adoptviaai",
    version="0.5.0",
    description="AdoptviaAI — AI adoption done right. CCA-F portfolio CLI.",
    author="AdoptviaAI",
    python_requires=">=3.10",
    packages=find_packages(),
    py_modules=["main"],          # main.py lives at the root, not inside a package
    install_requires=[
        "anthropic>=0.40.0",
        "click>=8.0.0",
        "python-dotenv>=1.0.0",
        "rich>=13.0.0",
    ],
    entry_points={
        "console_scripts": [
            "avai=main:cli",      # avai → main.py → cli() Click group
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)

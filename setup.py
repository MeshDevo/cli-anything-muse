from setuptools import find_packages, setup

setup(
    name="cli-anything-muse",
    version="1.0.0",
    description="Clean-room Muse-native CLI-Anything generator, validator, skill builder, and CLI-Hub client",
    python_requires=">=3.10",
    packages=find_packages(include=["cli_anything_muse", "cli_anything_muse.*"]),
    include_package_data=True,
    package_data={
        "cli_anything_muse": ["templates/*.template"],
    },
    install_requires=[
        "click>=8.0",
    ],
    extras_require={
        "interactive": ["prompt_toolkit>=3.0"],
        "templates": ["jinja2>=3.0"],
        "dev": ["pytest>=7.0"],
    },
    entry_points={
        "console_scripts": [
            "cli-anything-muse=cli_anything_muse.cli:main",
            "cli-hub=cli_anything_muse.cli_hub:main",
        ],
    },
)
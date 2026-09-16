from pathlib import Path
from setuptools import find_packages, setup

pkg = Path("src/runtime_feature_falsifier_cli")
data = [str(p.relative_to(pkg)) for p in (pkg / "payload").rglob("*") if p.is_file()]
setup(
    package_dir={"": "src"},
    packages=find_packages("src"),
    package_data={"runtime_feature_falsifier_cli": data},
    include_package_data=True,
)

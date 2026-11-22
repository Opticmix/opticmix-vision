from glob import glob
from setuptools import setup

package_name = "weevia_ros"
setup(
    name=package_name,
    version="0.0.1",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.py")),
        ("share/" + package_name + "/config", glob("config/*.yaml") + glob("config/*.xml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    extras_require={"test": ["pytest"]},
    entry_points={"console_scripts": [
        "sbs_player = weevia_ros.sbs_player:main",
        "sbs_splitter = weevia_ros.sbs_splitter:main",
        "weevia_driver = weevia_ros.weevia_driver:main",
    ]},
)

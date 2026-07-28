from setuptools import setup, find_packages

version = "0.1.7"

with open("README.md", "r", encoding="utf-8") as f:
    long_descr = f.read()

setup(
    name="pyhiqnet",
    packages=find_packages(),
    version=version,
    license="Apache 2.0",
    description="Async Python client for Crown DCi amplifiers via HiQnet",
    long_description=long_descr,
    long_description_content_type="text/markdown",
    author="johnno",
    url="https://github.com/johnno/py-hiqnet",
    download_url=f"https://github.com/johnno/py-hiqnet/archive/{version}.tar.gz",
    keywords=["Crown", "HiQnet", "DCi", "amplifier", "audio"],
    install_requires=[],
    entry_points={
        "console_scripts": [
            "crown-monitor=pyhiqnet.monitor:main",
        ],
    },
    python_requires=">=3.11",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3.11",
    ],
)

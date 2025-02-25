from setuptools import setup, find_packages
from os import path

def get_text_from_file(filename, fallback):
    try:
        with open(
            path.join(path.abspath(path.dirname(__file__)), filename), encoding="utf-8"
        ) as f:
            output = f.read()
    except Exception:
        output = fallback
    return output

PACKAGE_NAME = 'avp'
SOURCE_DIRECTORY = 'src'
PACKAGE_DESCRIPTION = 'Create audio visualization videos from a GUI or commandline'

setup(
    name='audio_visualizer_python',
    version='2.0.0',  # Get version from src/__init__.py
    url='https://github.com/djfun/audio-visualizer-python',
    license='MIT',
    description=PACKAGE_DESCRIPTION,
    author=get_text_from_file('AUTHORS', 'djfun, tassaron'),
    long_description=get_text_from_file('README.md', PACKAGE_DESCRIPTION),
    long_description_content_type='text/markdown',  # Add this!
    classifiers=[
        'Development Status :: 4 - Beta',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.10', # Specify supported Python versions
        'Intended Audience :: End Users/Desktop',
        'Topic :: Multimedia :: Video :: Non-Linear Editor',
    ],
    keywords=[
        'visualizer', 'visualization', 'commandline video',
        'video editor', 'ffmpeg', 'podcast'
    ],
    packages=find_packages(where=SOURCE_DIRECTORY), # Use find_packages with where
    package_dir={'': SOURCE_DIRECTORY}, # Use package_dir to map to src
    include_package_data=True,
    install_requires=[  # Removed specific versions
        'Pillow',
        'PyQt5',
        'numpy',
        'pytest',
        'pytest-qt',
    ],
    python_requires='>=3.10',  # Add this!
    entry_points={
        'console_scripts': [
            f'avp = {PACKAGE_NAME}.__main__:main'
        ],
    }
)
import os
import setuptools

setuptools.setup(
    name="mattermost-integration-gitlab",
    version="0.2.0",
    url="https://github.com/NotSqrt/mattermost-integration-gitlab",

    author="NotSqrt",
    author_email="notsqrt@gmail.com",

    description="GitLab Integration Service for Mattermost",
    long_description=open(os.path.join(os.path.dirname(__file__), 'README.md')).read(),

    packages=setuptools.find_packages(),

    install_requires=[
        "Flask==0.10.1",
        "requests",
        "six",
    ],

    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
    ],

    entry_points={
        'console_scripts': [
            'mattermost_gitlab = mattermost_gitlab.server:main',
        ]
    }
)

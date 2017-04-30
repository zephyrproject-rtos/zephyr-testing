#!/bin/bash
set -e

echo "- Install dependencies"
sudo apt-get install doxygen make
sudo pip install breathe sphinx awscli sphinx_rtd_theme

cd ${TESTING_REPO_STATE}
echo ${TESTING_REPO_STATE}
ls -la 
source zephyr-env.sh

cp -a /build/IN/docs-theme-repo/gitRepo doc/themes/zephyr-docs-theme
ls -la doc/themes

echo "- Building docs..."
make htmldocs > doc.log 2>&1
echo "- Uploading to AWS S3..."
aws s3 sync --quiet --delete doc/_build/html s3://zephyr-docs/online/dev

echo "Done"

#!/bin/bash
set -e

echo "- Install dependencies"
sudo apt-get install doxygen make
sudo pip install breathe sphinx
sudo pip install awscli

cd ${ZEPHYRREPO_STATE}
source zephyr-env.sh

cp -a /build/IN/docs-theme-repo/gitRepo doc/themes/zephyr-docs-theme
ls -la doc/themes

echo "- Building docs..."
make htmldocs > doc.log 2>&1
echo "Uploading to AWS S3"
aws s3 sync --delete doc/_build/html s3://zephyr-docs/online/dev

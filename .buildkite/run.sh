#!/bin/bash
echo "--- What does this do"
git log -n 5 --oneline --decorate --abbrev=12

# Setup module cache

# Setup links to cache
cd /workdir
ln -s /var/lib/buildkite-agent/zephyr-module-cache/modules
ln -s /var/lib/buildkite-agent/zephyr-module-cache/tools
ln -s /var/lib/buildkite-agent/zephyr-module-cache/bootloader
cd /workdir/zephyr

export JOB_NUM=$((${BUILDKITE_PARALLEL_JOB}+1))

if [ -n "${BUILDKITE_PULL_REQUEST_BASE_BRANCH}" ]; then
   ./scripts/ci/run_ci.sh  -c -b ${BUILDKITE_PULL_REQUEST_BASE_BRANCH} -r origin \
	   -m ${JOB_NUM} -M ${BUILDKITE_PARALLEL_JOB_COUNT} -p ${BUILDKITE_PULL_REQUEST}
else
   ./scripts/ci/run_ci.sh -c -b ${BUILDKITE_BRANCH} -r origin \
	   -m ${JOB_NUM} -M ${BUILDKITE_PARALLEL_JOB_COUNT};
fi;

SANITY_EXIT_STATUS=$?

# Rename sanitycheck junit xml for use with junit-annotate-buildkite-plugin
mv sanity-out/sanitycheck.xml sanitycheck-${BUILDKITE_JOB_ID}.xml
buildkite-agent artifact upload sanitycheck-${BUILDKITE_JOB_ID}.xml

exit ${SANITY_EXIT_STATUS}

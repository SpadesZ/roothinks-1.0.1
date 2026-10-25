#!/bin/sh
# SAI_TEST_PYTHON 包裝：在 fixture 容器內（Python 3.12、同一個 appuser）執行測試腳本的 sqlite 修正。
DOCKER_HOST=${DOCKER_HOST:-unix:///tmp/p312-docker.sock} exec docker exec -i p312-fx python "$@"

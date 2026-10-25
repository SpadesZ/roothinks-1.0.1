#!/bin/sh
# 在 V1（Debian、Python 3.12.15）容器啟動 sai_browser_fixture，主機 Chromium 跑 I12 的四次流程。
set -u
P=${P312_WORK:?設為工作目錄（含 ctx/、browser-repo/）}
V=${I12_BROWSER_DIR:?設為含 vendor/ 與 vendor-route.cjs 的目錄}
export DOCKER_HOST=${DOCKER_HOST:-unix:///tmp/p312-docker.sock}
mkdir -p $P/browser-out
for run in noplotly-aut:sai_autonomy_browser.cjs:0 noplotly-sci:sai_scientific_browser.cjs:0 assets-0:sai_assets_browser.cjs:0 assets-1:sai_assets_browser.cjs:1; do
  name=${run%%:*}; rest=${run#*:}; script=${rest%%:*}; plotly=${rest#*:}
  docker rm -f p312-fx >/dev/null 2>&1
  docker run -d --name p312-fx --network host -v /tmp:/tmp -v $P/ctx/test:/app/test:ro p312-v1:test \
    python test/integration_e2e/sai_browser_fixture.py --autonomy >/dev/null
  root=""
  for i in $(seq 1 120); do
    root=$(docker logs p312-fx 2>/dev/null | grep -m1 '^{"url"' | python3 -c 'import json,sys;print(json.loads(sys.stdin.read())["root"])' 2>/dev/null) && [ -n "$root" ] && break
    sleep 0.5
  done
  until curl -s -o /dev/null http://127.0.0.1:8893/auth/login; do sleep 0.5; done
  docker logs p312-fx > $P/browser-out/fx-$name.out 2>&1
  (cd $P/browser-repo && I12_VENDOR_DIR=$V/vendor I12_ROUTE_LOG=$P/browser-out/log-$name.json I12_SERVE_PLOTLY=$plotly \
    SAI_SYNTHETIC_ROOT=$root SAI_TEST_PYTHON=$(dirname $0)/py-in-fx.sh SAI_BROWSER_EXECUTABLE=/opt/pw-browsers/chromium-1243/chrome-linux64/chrome \
    /opt/node22/bin/node --require $V/vendor-route.cjs test/integration_e2e/$script > $P/browser-out/run-$name.out 2>&1)
  echo "$name exit=$? root=$root"
  docker logs p312-fx > $P/browser-out/fx-$name.out 2>&1
  docker exec p312-fx python -V >> $P/browser-out/fx-$name.out 2>&1
  docker rm -f p312-fx >/dev/null 2>&1
done

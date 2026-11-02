# Roothinks source maintenance contract
# 目的：R7 遷移演練發現：PAQ 問卷的 axis_labels／axis_tags／cube_data 不是預期型別時（舊資料或損壞），
#       專案流程狀態 /api/project/workflow/<pid> 整頁 500。狀態面板只顯示進度，應把異常值當成「尚未填寫」。
# 驗證：python -m pytest test/unit/test_workflow_status_r7.py -q
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_malformed_survey_fields_do_not_crash_workflow_status(tmp_path):
    from app.core_pro.workflow_status import build_workflow_status
    for labels, tags, cube in (("not-a-dict", "x", "y"), ({"a": "A"}, ["bad"], {"bad": 1}), (None, None, None)):
        survey = SimpleNamespace(axis_labels=labels, axis_tags=tags, cube_data=cube)
        status = build_workflow_status(str(tmp_path), "WF-p", project=None, survey=survey)
        assert isinstance(status, dict)


def test_valid_survey_still_counts_progress(tmp_path):
    from app.core_pro.workflow_status import build_workflow_status
    survey = SimpleNamespace(axis_labels={"x": "溫度", "y": "樣本", "z": "方法"}, axis_tags={"x": ["300 K"]}, cube_data=[{"c": 1}])
    status = build_workflow_status(str(tmp_path), "WF", project=None, survey=survey)
    assert "complete" in str(status)

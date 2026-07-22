#路徑(./app/project_portfolio/project_routes.py) #版本 v0.8 #更版時間 20260226-0010
"""
Project Routes (v0.8)
----------------------
負責處理專案管理相關的 API 請求，包含建立、讀取、更新與刪除 (CRUD)。
[v0.8 Update]:
1. 攔截 /create 路由中的 status='formal' 請求。
2. 若為新增正式專案，自動將 ID 加上 `-p`，強制寫入 formal 狀態與 AI 摘要預設值，
   並利用 os.rename 將原本的草案目錄更名，直接展開四大正式生命週期實體資料夾。
[v0.6 Update]:
1. 重構 /delete/<pid> 路由，實作 Cascade Delete (串聯刪除)，先清除 PaqSurvey/MetadataIndex 關聯，再刪除實體資料夾。
"""
import logging

from flask import Blueprint, request, jsonify, current_app, g
from werkzeug.exceptions import BadRequest, Forbidden, Unauthorized
from app import db
from app.models import Project, PaqSurvey, MetadataIndex, Paper
from app.project_portfolio.project_service import ProjectService
from app.core_pro.workflow_status import build_workflow_status
from app.services.formal_project_sync import ensure_formal_project_records, resolve_data_root
from app.security import check_ownership, enforce_project_ownership, safe_join_under, validate_id
import os
import shutil
import uuid

bp = Blueprint('project_portfolio', __name__, url_prefix='/api/project')
logger = logging.getLogger("ProjectRoutes")


def _base_pid(pid: str) -> str:
    p = str(pid or '').strip()
    if not p:
        return p
    return p[:-2] if p.endswith('-p') else p


def _formal_pid(pid: str) -> str:
    b = _base_pid(pid)
    if not b:
        return b
    return f"{b}-p"


def _safe_rmtree_atomic(base_dir: str, *parts: str) -> None:
    """
    Delete target path via atomic move to shrink symlink TOCTOU window.
    """
    base_real = os.path.realpath(base_dir)
    target_path = safe_join_under(base_real, *parts)
    if not os.path.lexists(target_path):
        return

    # Pre-check obvious symlink case.
    if os.path.islink(target_path):
        raise ValueError(f"Unsafe symlink target: {target_path}")

    staging_root = safe_join_under(base_real, ".delete_staging")
    os.makedirs(staging_root, exist_ok=True)
    staging_path = safe_join_under(staging_root, f"{uuid.uuid4().hex}")

    try:
        os.replace(target_path, staging_path)
    except FileNotFoundError:
        # Target may be concurrently removed; treat as successful cleanup.
        return

    # Post-move check to avoid following symlink payloads in rmtree.
    if os.path.islink(staging_path):
        os.unlink(staging_path)
        raise ValueError(f"Unsafe symlink target: {staging_path}")

    if os.path.isdir(staging_path):
        shutil.rmtree(staging_path)
    elif os.path.exists(staging_path):
        os.remove(staging_path)

@bp.route('/create', methods=['POST'])
def create_project():
    try:
        # 1. 取得並驗證請求資料
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'Invalid JSON payload'}), 400
            
        # 2. 驗證必填欄位
        if not data.get('name'):
            return jsonify({'success': False, 'message': 'Project name is required (專案名稱為必填)'}), 400

        # 3. 呼叫 Service 層執行業務邏輯 (這會先建出普通的 temp 專案與資料夾)
        project, msg = ProjectService.create_project(data)

        if project:
            # ========================================================
            # [v0.8] 核心擴充：處理直接新增「正式研究」的通道
            # ========================================================
            if data.get('status') == 'formal':
                old_pid = project.project_id
                
                # 自動附加 -p 後綴並更換狀態
                if not old_pid.endswith('-p'):
                    new_pid = f"{old_pid}-p"
                    project.project_id = new_pid
                    project.status = 'formal'
                    project.research_title = (
                        (project.research_title or '').strip()
                        or (project.context_background or '').strip()
                        or (project.name or '').strip()
                    )
                    project.ai_summary = "等待系統執行摘要..."
                    
                    # 處理實體資料夾的重新命名與結構建立
                    base_data_path = os.path.dirname(current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', ''))
                    if not os.path.isabs(base_data_path):
                         base_data_path = os.path.join(current_app.root_path, '../data')
                    base_data_path = os.path.realpath(base_data_path)
                         
                    old_dir = safe_join_under(base_data_path, old_pid)
                    new_dir = safe_join_under(base_data_path, new_pid)
                    
                    # 重新命名基底資料夾 (Service 層剛建好的 <pid> -> <pid-p>)
                    if os.path.exists(old_dir):
                        os.rename(old_dir, new_dir)
                    else:
                        os.makedirs(new_dir, exist_ok=True)
                        
                    # 直接展開正式專案的四大生命週期目錄
                    records_dir = safe_join_under(base_data_path, new_pid, 'literature', 'paq_records')
                    papers_dir = safe_join_under(base_data_path, new_pid, 'literature', 'papers')
                    os.makedirs(records_dir, exist_ok=True)
                    os.makedirs(papers_dir, exist_ok=True)
                    os.makedirs(safe_join_under(base_data_path, new_pid, 'study'), exist_ok=True)
                    os.makedirs(safe_join_under(base_data_path, new_pid, 'manuscript'), exist_ok=True)
                    os.makedirs(safe_join_under(base_data_path, new_pid, 'submit'), exist_ok=True)
                    
                    # 清除原本多餘的 paq 目錄
                    _safe_rmtree_atomic(base_data_path, new_pid, 'paq')
                    
                    db.session.commit()

                    # 同步建立 literature/paq_records 的啟動資料
                    ensure_formal_project_records(project)

            # 4. 成功回傳
            return jsonify({
                'success': True,
                'message': 'Project created successfully',
                'pid': project.project_id,
                'project': project.to_dict() 
            }), 201
        else:
            return jsonify({'success': False, 'message': f"Creation Failed: {msg}"}), 500

    except BadRequest as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': e.description or "Bad request"}), 400
    except Exception as e:
        db.session.rollback()
        logger.error("[Create Error] %s", e, exc_info=True)
        return jsonify({'success': False, 'message': "Internal server error"}), 500


@bp.route('/update/<pid>', methods=['POST'])
def update_project(pid):
    try:
        pid = validate_id(pid, "project_id")
        enforce_project_ownership(pid)
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'Invalid JSON payload'}), 400
            
        success, msg = ProjectService.update_project(pid, data)
        
        if success:
            return jsonify({
                'success': True, 
                'message': 'Project updated successfully'
            }), 200
        else:
            return jsonify({'success': False, 'message': msg}), 500

    except BadRequest as e:
        return jsonify({'success': False, 'message': e.description or "Bad request"}), 400
    except Unauthorized:
        return jsonify({'success': False, 'message': "Unauthorized"}), 401
    except Forbidden:
        return jsonify({'success': False, 'message': "Forbidden"}), 403
    except Exception as e:
        logger.error("[Update Error] %s", e, exc_info=True)
        return jsonify({'success': False, 'message': "Internal server error"}), 500


@bp.route('/list', methods=['GET'])
def list_projects():
    status = request.args.get('status', 'temp')
    try:
        # [Batch B] session 模式下傳入 user_id 以過濾有 membership 的專案
        user_id = None
        try:
            from flask_login import current_user as _cu
            from flask import current_app as _app
            if (
                str(_app.config.get("AUTH_MODE", "none")).strip().lower() == "session"
                and _cu.is_authenticated
            ):
                user_id = _cu.id
        except Exception:
            pass

        projects = ProjectService.get_projects(status=status, user_id=user_id)

        # Bearer 模式過濾（原邏輯保留）
        token = getattr(g, "auth_token", "")
        if token and user_id is None:
            filtered = []
            for p in projects:
                pid = (p.get("project_id") or p.get("pid") or "").strip()
                if not pid:
                    continue
                if check_ownership(token, pid):
                    filtered.append(p)
            projects = filtered

        return jsonify({
            'success': True,
            'projects': projects,
            'count': len(projects)
        }), 200
    except Exception as e:
        logger.error("[List Error] %s", e, exc_info=True)
        return jsonify({'success': False, 'message': "Internal server error"}), 500


@bp.route('/workflow/<pid>', methods=['GET'])
def project_workflow(pid):
    try:
        pid = validate_id(pid, "project_id")
        enforce_project_ownership(pid)
        project = Project.query.filter_by(project_id=pid).first()
        if not project:
            return jsonify({'success': False, 'message': 'Project not found'}), 404
        survey = PaqSurvey.query.filter_by(project_ref_id=project.id).first()
        payload = build_workflow_status(resolve_data_root(), pid, project=project, survey=survey)
        payload['success'] = True
        return jsonify(payload), 200
    except BadRequest as e:
        return jsonify({'success': False, 'message': e.description or "Bad request"}), 400
    except Unauthorized:
        return jsonify({'success': False, 'message': "Unauthorized"}), 401
    except Forbidden:
        return jsonify({'success': False, 'message': "Forbidden"}), 403
    except Exception as e:
        logger.error("[Workflow Error] %s", e, exc_info=True)
        return jsonify({'success': False, 'message': "Internal server error"}), 500


@bp.route('/delete/<pid>', methods=['DELETE'])
def delete_project(pid):
    try:
        # Lazy import to avoid blueprint import-order coupling.
        from app.core_pro.manuscript.model_section import ManuSectionConfig
        from app.core_pro.manuscript.model_manu import ManuProject, ManuSectionLock, ManuMember

        pid = validate_id(pid, "project_id")
        enforce_project_ownership(pid)

        body = request.get_json(silent=True) or {}
        raw_scope = (
            (request.args.get('scope') or '').strip().lower()
            or str(body.get('scope') or '').strip().lower()
        )
        scope = raw_scope or 'single'
        if scope not in {'single', 'pair'}:
            return jsonify({'success': False, 'message': "Invalid scope. Use 'single' or 'pair'."}), 400

        base_pid = _base_pid(pid)
        formal_pid = _formal_pid(pid)

        pair_ids = list(dict.fromkeys([x for x in [base_pid, formal_pid] if x]))
        delete_ids = pair_ids if scope == 'pair' else [pid]

        # Legacy cleanup: historical bug may have created data/<formal>-p.
        if scope == 'pair':
            legacy_double_pid = f"{formal_pid}-p" if formal_pid else ""
            dir_ids = list(dict.fromkeys([x for x in [*delete_ids, legacy_double_pid] if x]))
        else:
            legacy_for_single = f"{pid}-p" if pid.endswith('-p') else ""
            dir_ids = list(dict.fromkeys([x for x in [*delete_ids, legacy_for_single] if x]))

        projects = Project.query.filter(Project.project_id.in_(delete_ids)).all()
        base_data_path = resolve_data_root()

        target_dirs = []
        for x in dir_ids:
            raw_target_dir = os.path.join(base_data_path, str(x))
            if os.path.lexists(raw_target_dir) and os.path.islink(raw_target_dir):
                raise ValueError(f"Unsafe symlink target: {raw_target_dir}")
            target_dirs.append(safe_join_under(base_data_path, str(x)))
        has_any_dir = any(os.path.exists(d) for d in target_dirs)
        has_any_section_cfg = (
            db.session.query(ManuSectionConfig.id)
            .filter(ManuSectionConfig.pid.in_(delete_ids))
            .first() is not None
        )
        has_any_manu_project = (
            db.session.query(ManuProject.id)
            .filter(ManuProject.pid.in_(delete_ids))
            .first() is not None
        )

        if not projects and not has_any_dir and not has_any_section_cfg and not has_any_manu_project:
            return jsonify({'success': False, 'message': 'Project not found'}), 404

        # 1) Main DB cascade
        Paper.query.filter(Paper.pid.in_(delete_ids)).delete(synchronize_session=False)
        # [Batch B] 清除 workspace_members
        try:
            from app.models import WorkspaceMember
            WorkspaceMember.query.filter(WorkspaceMember.pid.in_(delete_ids)).delete(synchronize_session=False)
        except Exception:
            logger.warning("Failed to delete workspace_members for delete_ids=%s", delete_ids, exc_info=True)
        for project in projects:
            PaqSurvey.query.filter_by(project_ref_id=project.id).delete()
            MetadataIndex.query.filter_by(project_ref_id=project.id).delete()
            db.session.delete(project)

        # 2) Manuscript DB cascade
        manu_projects = ManuProject.query.filter(ManuProject.pid.in_(delete_ids)).all()
        for manu_project in manu_projects:
            ManuSectionLock.query.filter_by(project_id=manu_project.id).delete()
            ManuMember.query.filter_by(project_id=manu_project.id).delete()
            db.session.delete(manu_project)

        ManuSectionConfig.query.filter(ManuSectionConfig.pid.in_(delete_ids)).delete(synchronize_session=False)

        # 3) Filesystem cleanup (also clears legacy accidental data/<pid>-p-p)
        for target_dir in target_dirs:
            if os.path.exists(target_dir):
                rel_target = os.path.relpath(target_dir, start=base_data_path)
                _safe_rmtree_atomic(base_data_path, rel_target)

        db.session.commit()
        return jsonify({
            'success': True, 
            'message': f'Projects {delete_ids} deleted successfully (scope={scope})',
            'scope': scope,
            'deleted_ids': delete_ids
        }), 200
            
    except BadRequest as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': e.description or "Bad request"}), 400
    except Unauthorized:
        db.session.rollback()
        return jsonify({'success': False, 'message': "Unauthorized"}), 401
    except Forbidden:
        db.session.rollback()
        return jsonify({'success': False, 'message': "Forbidden"}), 403
    except Exception as e:
        db.session.rollback()
        logger.error("[Delete Error] %s", e, exc_info=True)
        return jsonify({'success': False, 'message': "Internal server error"}), 500

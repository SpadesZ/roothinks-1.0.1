#路徑(./app/core_pro/paq/paq_routes.py) #版本 v2.6 #更版時間 20260226-0600
from flask import Blueprint, request, jsonify, current_app
from app import db
from app.models import Project, PaqSurvey, MetadataIndex
from app.core_pro.paq.paq_core import PaqCore
# [v1.8 Fix] 安全引入 PaqMatrix (避免 ImportError 導致整個 Blueprint 崩潰)
try:
    from app.core_pro.paq.paq_matrix import PaqMatrix
    HAS_PAQ_MATRIX = True
except ImportError:
    HAS_PAQ_MATRIX = False
    logger.warning("[PAQ Routes] Warning: paq_matrix not found, cube returns raw voxels.")
from app.llm_service.matching_tasks import task_1paqswot, task_2cubegen, task2A_paqchat
# [v1.8 Fix] promote 需要 IndexService
from app.services.index_service import IndexService
from app.services.formal_project_sync import ensure_formal_project_records, resolve_data_root
import json
import ast
import os
import shutil
import glob
import logging
from werkzeug.exceptions import BadRequest, Forbidden, Unauthorized
from app.security import validate_id, enforce_project_ownership

bp = Blueprint('paq', __name__, url_prefix='/api/paq')
logger = logging.getLogger("PaqRoutes")


def _safe_pid(raw_pid: str) -> str:
    p = validate_id(raw_pid, "project_id")
    enforce_project_ownership(p)
    return p

#V2.3修改起點 ===========================================================================
@bp.route('/status/<pid>', methods=['GET'])
def get_paq_status(pid):
    """ 取得 PAQ 專案狀態與基本資訊 """
    try:
        pid = _safe_pid(pid)
        project = Project.query.filter_by(project_id=pid).first()
        if not project:
            return jsonify({'success': False, 'message': 'Project not found'}), 404

        survey = PaqSurvey.query.filter_by(project_ref_id=project.id).first()
        
        pi_name = "Unknown"
        members_data = project.members
        
        # 1. 資料清洗與型別轉換
        if members_data:
            if isinstance(members_data, str):
                try:
                    members_data = json.loads(members_data)
                except json.JSONDecodeError:
                    try:
                        members_data = ast.literal_eval(members_data)
                    except Exception:
                        members_data = []

        # 2. 搜尋 PI 並執行【後端強制英文全名萃取】
        if isinstance(members_data, list):
            for m in members_data:
                if isinstance(m, dict):
                    role = m.get('role', '')
                
                    # 1. 精準比對PI 角色
                    if role.startswith('主持人') or (('PI' in role or '主持人' in role) and 'Co-PI' not in role and '共同' not in role):
                        raw_name = m.get('name', 'Unknown')
                                
                        if isinstance(raw_name, dict):
                            # [v1.9 核心邏輯] 優先處理三層嵌套的英文節點 (English First)
                            en_node = raw_name.get('en') or raw_name.get('english') or raw_name.get('English')
                        
                            # 2. 優先萃取英文姓名
                            if isinstance(en_node, dict):
                                given = en_node.get('given') or en_node.get('first') or ''
                                surname = en_node.get('surname') or en_node.get('last') or en_node.get('family') or ''
                                if given or surname:
                                    pi_name = f"{surname} {given}".strip()
                        
                            elif isinstance(en_node, str) and en_node.strip():
                                pi_name = en_node.strip()
                                    
                            # 3. 若無英文，退而求其次抓中文
                            if pi_name == 'Unknown' or not isinstance(pi_name, str):
                                orig_node = raw_name.get('original') or raw_name.get('zh') or raw_name.get('native')
                                if isinstance(orig_node, dict):
                                    surname = orig_node.get('surname') or orig_node.get('last') or ''
                                    given = orig_node.get('given') or orig_node.get('first') or ''
                                    if surname or given:
                                        pi_name = f"{surname}{given}".strip()

                        elif isinstance(raw_name, str) and raw_name.strip():
                            pi_name = raw_name.strip()
                        
                        break
                          
        response_data = {
            'success': True,
            'project_id': project.project_id,  
            'name': project.name,              
            'pi_name': pi_name,               
            'status': project.status,
            'research_title': project.research_title,
            'members': members_data if isinstance(members_data, list) else [],
            'survey': {
                'axis_labels': survey.axis_labels if survey else {},
                'axis_tags': survey.axis_tags if survey else {},
                'cube_data': survey.cube_data if survey else None
            }
        }
        return jsonify(response_data), 200

    except Exception as e:
        if isinstance(e, BadRequest):
            return jsonify({'success': False, 'message': 'Invalid request'}), 400
        if isinstance(e, Forbidden):
            return jsonify({'success': False, 'message': 'Forbidden'}), 403
        if isinstance(e, Unauthorized):
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        logger.error("[PAQ Status Error] %s", e, exc_info=True)
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@bp.route('/save_taxonomy', methods=['POST'])
def save_taxonomy():
    """ 專屬手動存檔通道 """
    try:
        data = request.get_json()
        pid = _safe_pid(data.get('pid'))
        labels = data.get('labels', {})
        tags = data.get('tags', {})

        project = Project.query.filter_by(project_id=pid).first()
        if not project:
            return jsonify({'success': False, 'message': 'Project not found'}), 404

        # [v2.6 Security] 阻擋唯讀專案的修改
        if project.status == 'readonly':
            return jsonify({'success': False, 'message': 'Project is locked (Read-Only).'}), 403

        taxonomy_data = {
            'axis_labels': labels,
            'axis_tags': tags
        }

        PaqCore._update_survey_data(project.id, taxonomy=taxonomy_data)
        # [Path Convergence] 新檔案寫入單層 data/<pid>/paq/，並同步舊層路徑副本維持相容。
        PaqCore._save_json(pid, '', 'taxonomy_manual_update.json', taxonomy_data)
        PaqCore._save_json(pid, 'paq', 'taxonomy_manual_update.json', taxonomy_data)

        return jsonify({'success': True, 'message': 'Manual taxonomy saved successfully.'}), 200

    except Exception as e:
        if isinstance(e, BadRequest):
            return jsonify({'success': False, 'message': 'Invalid request'}), 400
        if isinstance(e, Forbidden):
            return jsonify({'success': False, 'message': 'Forbidden'}), 403
        if isinstance(e, Unauthorized):
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        logger.error("[Save Taxonomy Error] %s", e, exc_info=True)
        return jsonify({'success': False, 'message': "Internal server error"}), 500

@bp.route('/run_task', methods=['POST'])
def run_paq_task():
    """ 執行 PAQ AI 任務 """
    try:
        data = request.get_json(silent=True) or {}
        pid = _safe_pid(data.get('pid'))
        task_id = data.get('task_id')
        input_data = data.get('input_data', {})
        project = Project.query.filter_by(project_id=pid).first()
        if not project:
            return jsonify({'success': False, 'message': 'Project not found'}), 404
            
        # [v2.6 Security] 阻擋唯讀專案執行任何 AI 任務
        if project.status == 'readonly':
            return jsonify({'success': False, 'message': 'Project is locked (Read-Only). AI execution disabled.'}), 403
        
        context = project.to_dict()

        if task_id == 'task_1paqswot':
            mode = input_data.get('mode', 'fresh')
            user_edits = input_data.get('user_edits', {})
            
            success, result = task_1paqswot.execute_paq_taxonomy(context, mode=mode, user_edits=user_edits)
            if success:
                PaqCore._update_survey_data(project.id, taxonomy=result)
                PaqCore._save_json(pid, 'paq', 'taxonomy_v1.json', result)
                return jsonify({'success': True, 'data': result})
            else:
                return jsonify({'success': False, 'message': result}), 500

        elif task_id == 'task_2cubegen':
            taxonomy_context = {
                'axis_labels': input_data.get('labels', {}),
                'axis_tags': input_data.get('tags', {})
            }
            
            success, result = task_2cubegen.execute_cube_gen(context, taxonomy_context)
            if success:
                if HAS_PAQ_MATRIX:
                    matrix_data = PaqMatrix.transform(taxonomy_context, result)
                    view_data = matrix_data.get('view_data', [])
                else:
                    view_data = result.get('voxels', [])
                
                PaqCore._update_survey_data(project.id, cube={'voxels': view_data})
                PaqCore._save_json(pid, 'paq', 'cube_v1.json', result)
                
                return jsonify({'success': True, 'data': {'view_data': view_data}})
            else:
                return jsonify({'success': False, 'message': result}), 500

        elif task_id == 'task_2a_chat':
            user_msg = input_data.get('user_input')
            chat_history = input_data.get('chat_history', [])
            
            success, result = task2A_paqchat.execute_paq_chat(context, chat_history, user_msg)
            
            if success:
                reply_content = result.get('reply') if isinstance(result, dict) else str(result)
                return jsonify({'success': True, 'data': {'reply': reply_content}})
            else:
                return jsonify({'success': False, 'message': result}), 500

        else:
            return jsonify({'success': False, 'message': f'Unknown Task ID: {task_id}'}), 400

    except Exception as e:
        if isinstance(e, BadRequest):
            return jsonify({'success': False, 'message': 'Invalid request'}), 400
        if isinstance(e, Forbidden):
            return jsonify({'success': False, 'message': 'Forbidden'}), 403
        if isinstance(e, Unauthorized):
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        logger.error("[Run Task Error] %s", e, exc_info=True)
        return jsonify({'success': False, 'message': "Internal server error"}), 500

@bp.route('/promote', methods=['POST'])
def promote_project():
    try:
        data = request.get_json(silent=True) or {}
        pid = _safe_pid(data.get('pid'))
        final_topic = data.get('final_topic')
        
        project = Project.query.filter_by(project_id=pid).first()
        if not project:
            return jsonify({'success': False, 'message': 'Project not found'}), 404
            
        # [v2.6 Security] 防止已被 promote 過的唯讀專案重複執行
        if project.status == 'readonly':
            return jsonify({'success': False, 'message': 'Project is already promoted.'}), 403
            
        new_pid = f"{pid}-p"
        
        existing_p = Project.query.filter_by(project_id=new_pid).first()
        if existing_p:
            return jsonify({'success': False, 'message': 'Promoted project already exists.'}), 400

        resolved_title = (
            (final_topic or '').strip()
            or (project.name or '').strip()
            or (project.research_title or '').strip()
            or "Untitled Project"
        )
            
        # 專案名稱即研究題目：轉正 modal 填的 final topic 要同時成為 name。
        # 只寫 research_title 的話，正式卡片顯示 final topic、編輯視窗卻顯示
        # 舊的 name，使用者一改題目就會發現「改了沒反應」——實際上是改到了
        # 另一個欄位。兩欄從轉正這一刻起就必須一致。
        new_project = Project(
            project_id=new_pid,
            name=resolved_title,
            abbreviation=project.abbreviation,
            research_title=resolved_title,
            status='formal',
            classification=project.classification,
            keywords=project.keywords,
            context_background=project.context_background,
            ai_summary="等待系統執行摘要...", 
            members=project.members
        )
        db.session.add(new_project)
        db.session.flush() 
        
        old_survey = PaqSurvey.query.filter_by(project_ref_id=project.id).first()
        if old_survey:
            new_survey = PaqSurvey(
                project_ref_id=new_project.id,
                axis_labels=old_survey.axis_labels,
                axis_tags=old_survey.axis_tags,
                cube_data=old_survey.cube_data
            )
            db.session.add(new_survey)
            
        project.status = 'readonly'

        base_data_path = resolve_data_root()

        old_paq_dir = os.path.join(base_data_path, str(pid), 'paq')
        new_base_dir = os.path.join(base_data_path, new_pid)
        
        records_dir = os.path.join(new_base_dir, 'literature', 'paq_records')
        papers_dir = os.path.join(new_base_dir, 'literature', 'papers')
        os.makedirs(records_dir, exist_ok=True)
        os.makedirs(papers_dir, exist_ok=True)
        os.makedirs(os.path.join(new_base_dir, 'study'), exist_ok=True)
        os.makedirs(os.path.join(new_base_dir, 'manuscript'), exist_ok=True)
        os.makedirs(os.path.join(new_base_dir, 'submit'), exist_ok=True)

        if os.path.exists(old_paq_dir):
            tax_files = glob.glob(os.path.join(old_paq_dir, 'paq_taxonomy', '*.json'))
            if tax_files:
                latest_tax = max(tax_files, key=os.path.getmtime)
                shutil.copy(latest_tax, records_dir)
                
            cube_files = glob.glob(os.path.join(old_paq_dir, 'paq_matrix', '*.json'))
            if cube_files:
                latest_cube = max(cube_files, key=os.path.getmtime)
                shutil.copy(latest_cube, records_dir)
                
            old_chat_dir = os.path.join(old_paq_dir, 'chat')
            if os.path.exists(old_chat_dir):
                shutil.copytree(old_chat_dir, os.path.join(records_dir, 'chat'))

        ensure_formal_project_records(new_project)

        # [Batch B] 複製原 pid 的所有 membership 到 new_pid
        try:
            from app.models import WorkspaceMember, ROLE_ORDER
            old_members = WorkspaceMember.query.filter_by(pid=pid).all()
            for m in old_members:
                # 若 new_pid 尚無此 user 的 membership 則複製
                exists = WorkspaceMember.query.filter_by(
                    user_id=m.user_id, pid=new_pid
                ).first()
                if not exists:
                    new_m = WorkspaceMember(
                        user_id=m.user_id,
                        pid=new_pid,
                        role=m.role,
                    )
                    db.session.add(new_m)
        except Exception:
            logger.warning("[Promote] copy memberships failed for pid=%s -> %s", pid, new_pid, exc_info=True)

        db.session.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        if isinstance(e, BadRequest):
            return jsonify({'success': False, 'message': 'Invalid request'}), 400
        if isinstance(e, Forbidden):
            return jsonify({'success': False, 'message': 'Forbidden'}), 403
        if isinstance(e, Unauthorized):
            return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        db.session.rollback()
        logger.error("[Promote Error] %s", e, exc_info=True)
        return jsonify({'success': False, 'message': "Internal server error"}), 500

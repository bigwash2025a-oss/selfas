#!/usr/bin/env python3
"""
셀프세차장 실시간 AS 소통 시스템 백엔드
- FastAPI + WebSocket
- SQLite 데이터베이스
- 실시간 채팅
- 파일 업로드
- 상태 관리
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
from datetime import datetime
import sqlite3
import json
import uuid
import os
import hashlib
import asyncio
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

# 앱 초기화
app = FastAPI(title="셀프세차장 AS 시스템", version="1.0.0")

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 디렉토리 설정
BASE_DIR = Path(__file__).parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
DB_PATH = BASE_DIR / "as_system.db"
LOG_DIR = BASE_DIR / "logs"

UPLOAD_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# ==================== 로깅 설정 ====================

# 로거 설정
logger = logging.getLogger("AS_SYSTEM")
logger.setLevel(logging.INFO)

# 파일 핸들러 (10MB마다 로테이션, 최대 5개 파일 유지)
file_handler = RotatingFileHandler(
    LOG_DIR / "as_system.log",
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5,
    encoding='utf-8'
)
file_handler.setLevel(logging.INFO)

# 포맷 설정
formatter = logging.Formatter(
    '%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# 콘솔 핸들러도 추가
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

def get_client_ip(request: Request) -> str:
    """클라이언트 IP 주소 추출"""
    # X-Forwarded-For 헤더 확인 (프록시 뒤에 있을 경우)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()

    # X-Real-IP 헤더 확인
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip

    # 직접 연결된 클라이언트 IP
    return request.client.host if request.client else "unknown"

# ==================== 데이터베이스 초기화 ====================

def init_db():
    """데이터베이스 초기화"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # AS 요청 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS as_requests (
            id TEXT PRIMARY KEY,
            bay_number INTEGER NOT NULL,
            equipment_type TEXT NOT NULL,
            problem_type TEXT NOT NULL,
            diagnosis_result TEXT,
            customer_name TEXT NOT NULL,
            customer_phone TEXT NOT NULL,
            customer_vehicle TEXT,
            status TEXT DEFAULT 'pending',
            assigned_technician TEXT,
            priority TEXT DEFAULT 'normal',
            created_at TEXT NOT NULL,
            assigned_at TEXT,
            started_at TEXT,
            completed_at TEXT,
            confirmed_at TEXT
        )
    """)

    # 채팅 메시지 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            sender_type TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            message_type TEXT DEFAULT 'text',
            content TEXT NOT NULL,
            file_url TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (request_id) REFERENCES as_requests(id)
        )
    """)

    # 상태 변경 히스토리 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS status_history (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT NOT NULL,
            changed_by TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (request_id) REFERENCES as_requests(id)
        )
    """)

    # 첨부파일 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attachments (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            message_id TEXT,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            uploaded_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (request_id) REFERENCES as_requests(id)
        )
    """)

    # 사용자 활동 로그 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_activity_logs (
            id TEXT PRIMARY KEY,
            ip_address TEXT NOT NULL,
            user_agent TEXT,
            action_type TEXT NOT NULL,
            action_detail TEXT,
            search_keyword TEXT,
            page_url TEXT,
            bay_number INTEGER,
            request_id TEXT,
            session_id TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # 출동 스케줄 테이블
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS visit_schedules (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            proposed_by TEXT NOT NULL,
            proposed_date TEXT NOT NULL,
            proposed_time TEXT NOT NULL,
            status TEXT DEFAULT 'proposed',
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY (request_id) REFERENCES as_requests(id)
        )
    """)

    # 인덱스 생성
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_requests_status ON as_requests(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_request ON chat_messages(request_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_request ON status_history(request_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_ip ON user_activity_logs(ip_address)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_action ON user_activity_logs(action_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_date ON user_activity_logs(created_at)")

    conn.commit()
    conn.close()

# 앱 시작 시 DB 초기화
init_db()

# ==================== Pydantic 모델 ====================

class ASRequest(BaseModel):
    bay_number: int
    equipment_type: str
    problem_type: str
    diagnosis_result: Optional[str] = None
    customer_name: str
    customer_phone: str
    customer_vehicle: Optional[str] = None
    priority: str = "normal"

class ASRequestUpdate(BaseModel):
    status: Optional[str] = None
    assigned_technician: Optional[str] = None
    priority: Optional[str] = None

class ChatMessage(BaseModel):
    request_id: str
    sender_type: str  # 'customer' or 'technician'
    sender_name: str
    message_type: str = "text"  # 'text', 'image', 'file', 'voice'
    content: str
    file_url: Optional[str] = None

class StatusChange(BaseModel):
    request_id: str
    to_status: str
    changed_by: str
    note: Optional[str] = None

class UserActivityLog(BaseModel):
    action_type: str  # page_view, search, as_request, chat_send, wizard_start, manual_view, etc.
    action_detail: Optional[str] = None
    search_keyword: Optional[str] = None
    page_url: Optional[str] = None
    bay_number: Optional[int] = None
    request_id: Optional[str] = None
    session_id: Optional[str] = None

class VisitSchedule(BaseModel):
    request_id: str
    proposed_by: str  # 'customer' or 'technician'
    proposed_date: str  # YYYY-MM-DD
    proposed_time: str  # HH:MM
    notes: Optional[str] = None

class VisitScheduleUpdate(BaseModel):
    status: Optional[str] = None  # 'proposed', 'accepted', 'rejected', 'confirmed'
    proposed_date: Optional[str] = None
    proposed_time: Optional[str] = None
    notes: Optional[str] = None

# ==================== WebSocket 연결 관리 ====================

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.technician_connections: List[WebSocket] = []  # 기사 전용 연결

    async def connect(self, websocket: WebSocket, request_id: str):
        await websocket.accept()
        if request_id not in self.active_connections:
            self.active_connections[request_id] = []
        self.active_connections[request_id].append(websocket)

    def disconnect(self, websocket: WebSocket, request_id: str):
        if request_id in self.active_connections:
            self.active_connections[request_id].remove(websocket)

    async def connect_technician(self, websocket: WebSocket):
        """기사 대시보드 연결"""
        await websocket.accept()
        self.technician_connections.append(websocket)

    def disconnect_technician(self, websocket: WebSocket):
        """기사 대시보드 연결 해제"""
        if websocket in self.technician_connections:
            self.technician_connections.remove(websocket)

    async def send_message(self, message: dict, request_id: str):
        if request_id in self.active_connections:
            for connection in self.active_connections[request_id]:
                try:
                    await connection.send_json(message)
                except:
                    pass

    async def broadcast_to_technicians(self, message: dict):
        """모든 기사에게 브로드캐스트 (새 요청 알림)"""
        dead_connections = []
        for connection in self.technician_connections:
            try:
                await connection.send_json(message)
            except:
                dead_connections.append(connection)

        # 끊어진 연결 제거
        for conn in dead_connections:
            self.disconnect_technician(conn)

manager = ConnectionManager()

# ==================== 유틸리티 함수 ====================

def get_db():
    """데이터베이스 연결 가져오기"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def generate_id():
    """UUID 기반 ID 생성"""
    return str(uuid.uuid4())

def now():
    """현재 시간 ISO 형식 반환"""
    return datetime.now().isoformat()

def get_client_ip(request: Request):
    """클라이언트 IP 주소 가져오기 (프록시 고려)"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"

# ==================== API 엔드포인트 ====================

@app.get("/")
async def root():
    """API 상태 확인"""
    return {
        "service": "셀프세차장 AS 시스템",
        "version": "1.0.0",
        "status": "running"
    }

@app.post("/api/as-requests")
async def create_as_request(as_request: ASRequest, http_request: Request):
    """AS 요청 생성"""
    client_ip = get_client_ip(http_request)

    conn = get_db()
    cursor = conn.cursor()

    request_id = generate_id()

    # 로그 기록
    logger.info(f"[AS 요청 생성] ID: {request_id} | IP: {client_ip} | "
                f"고객: {as_request.customer_name} ({as_request.customer_phone}) | "
                f"베이: {as_request.bay_number}번 | 장비: {as_request.equipment_type} | "
                f"문제: {as_request.problem_type}")

    cursor.execute("""
        INSERT INTO as_requests (
            id, bay_number, equipment_type, problem_type, diagnosis_result,
            customer_name, customer_phone, customer_vehicle, priority, created_at, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (
        request_id, as_request.bay_number, as_request.equipment_type, as_request.problem_type,
        as_request.diagnosis_result, as_request.customer_name, as_request.customer_phone,
        as_request.customer_vehicle, as_request.priority, now()
    ))

    # 상태 히스토리 기록
    cursor.execute("""
        INSERT INTO status_history (id, request_id, from_status, to_status, changed_by, created_at)
        VALUES (?, ?, NULL, 'pending', 'system', ?)
    """, (generate_id(), request_id, now()))

    conn.commit()

    # 생성된 요청 조회
    cursor.execute("SELECT * FROM as_requests WHERE id = ?", (request_id,))
    row = cursor.fetchone()
    conn.close()

    result = dict(row)

    # 모든 기사에게 새 요청 알림
    await manager.broadcast_to_technicians({
        "type": "new_request",
        "data": result
    })

    return result

@app.get("/api/as-requests")
async def get_as_requests(status: Optional[str] = None):
    """AS 요청 목록 조회"""
    conn = get_db()
    cursor = conn.cursor()

    if status:
        cursor.execute("SELECT * FROM as_requests WHERE status = ? ORDER BY created_at DESC", (status,))
    else:
        cursor.execute("SELECT * FROM as_requests ORDER BY created_at DESC")

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

@app.get("/api/as-requests/{request_id}")
async def get_as_request(request_id: str):
    """특정 AS 요청 조회"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM as_requests WHERE id = ?", (request_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="AS 요청을 찾을 수 없습니다")

    result = dict(row)

    # 채팅 메시지 포함
    cursor.execute("""
        SELECT * FROM chat_messages WHERE request_id = ? ORDER BY created_at ASC
    """, (request_id,))
    result['messages'] = [dict(msg) for msg in cursor.fetchall()]

    # 상태 히스토리 포함
    cursor.execute("""
        SELECT * FROM status_history WHERE request_id = ? ORDER BY created_at ASC
    """, (request_id,))
    result['history'] = [dict(h) for h in cursor.fetchall()]

    # 진단 히스토리 포함 (사용자 활동 로그에서)
    cursor.execute("""
        SELECT * FROM user_activity_logs
        WHERE request_id = ?
        AND action_type IN ('wizard_start', 'symptom_select', 'wizard_complete')
        ORDER BY created_at ASC
    """, (request_id,))
    result['diagnosis_history'] = [dict(log) for log in cursor.fetchall()]

    # 첨부파일 포함
    cursor.execute("""
        SELECT * FROM attachments WHERE request_id = ? ORDER BY created_at ASC
    """, (request_id,))
    result['attachments'] = [dict(att) for att in cursor.fetchall()]

    # 출동 스케줄 포함
    cursor.execute("""
        SELECT * FROM visit_schedules WHERE request_id = ? ORDER BY created_at DESC
    """, (request_id,))
    result['visit_schedules'] = [dict(sch) for sch in cursor.fetchall()]

    conn.close()

    return result

@app.patch("/api/as-requests/{request_id}")
async def update_as_request(request_id: str, update: ASRequestUpdate):
    """AS 요청 업데이트"""
    conn = get_db()
    cursor = conn.cursor()

    # 현재 상태 조회
    cursor.execute("SELECT status FROM as_requests WHERE id = ?", (request_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="AS 요청을 찾을 수 없습니다")

    old_status = row[0]

    # 업데이트할 필드 준비
    updates = []
    values = []

    if update.status:
        updates.append("status = ?")
        values.append(update.status)

        # 상태별 시간 기록
        if update.status == "assigned":
            updates.append("assigned_at = ?")
            values.append(now())
        elif update.status == "in_progress":
            updates.append("started_at = ?")
            values.append(now())
        elif update.status == "completed":
            updates.append("completed_at = ?")
            values.append(now())
        elif update.status == "confirmed":
            updates.append("confirmed_at = ?")
            values.append(now())

    if update.assigned_technician:
        updates.append("assigned_technician = ?")
        values.append(update.assigned_technician)

    if update.priority:
        updates.append("priority = ?")
        values.append(update.priority)

    if updates:
        values.append(request_id)
        cursor.execute(f"""
            UPDATE as_requests SET {', '.join(updates)} WHERE id = ?
        """, values)

        # 상태 변경 히스토리 기록
        if update.status and update.status != old_status:
            cursor.execute("""
                INSERT INTO status_history (id, request_id, from_status, to_status, changed_by, created_at)
                VALUES (?, ?, ?, ?, 'system', ?)
            """, (generate_id(), request_id, old_status, update.status, now()))

        conn.commit()

    # 업데이트된 요청 조회
    cursor.execute("SELECT * FROM as_requests WHERE id = ?", (request_id,))
    row = cursor.fetchone()
    conn.close()

    result = dict(row)

    # WebSocket으로 업데이트 알림
    await manager.send_message({
        "type": "request_updated",
        "data": result
    }, request_id)

    # 긴급출동 요청 시 모든 기사에게 알림
    if update.status == "needs_visit":
        print(f"🚨 긴급출동 broadcast: {result['id']}, 연결된 기사 수: {len(manager.technician_connections)}")
        await manager.broadcast_to_technicians({
            "type": "urgent_visit_request",
            "data": result
        })
        print(f"✅ broadcast 완료")

    return result

@app.post("/api/chat-messages")
async def create_chat_message(message: ChatMessage, http_request: Request):
    """채팅 메시지 생성"""
    client_ip = get_client_ip(http_request)

    conn = get_db()
    cursor = conn.cursor()

    message_id = generate_id()

    # 로그 기록
    content_preview = message.content[:50] + "..." if len(message.content) > 50 else message.content
    logger.info(f"[채팅 메시지] IP: {client_ip} | "
                f"요청ID: {message.request_id[:8]}... | "
                f"발신: {message.sender_type} ({message.sender_name}) | "
                f"내용: {content_preview}")

    cursor.execute("""
        INSERT INTO chat_messages (
            id, request_id, sender_type, sender_name, message_type, content, file_url, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        message_id, message.request_id, message.sender_type, message.sender_name,
        message.message_type, message.content, message.file_url, now()
    ))

    conn.commit()

    cursor.execute("SELECT * FROM chat_messages WHERE id = ?", (message_id,))
    row = cursor.fetchone()
    conn.close()

    result = dict(row)

    # WebSocket으로 실시간 전송
    await manager.send_message({
        "type": "new_message",
        "data": result
    }, message.request_id)

    return result

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    request_id: str = Form(...),
    uploaded_by: str = Form(...),
    http_request: Request = None
):
    """파일 업로드"""
    client_ip = get_client_ip(http_request) if http_request else "unknown"

    # 파일 저장
    file_id = generate_id()
    file_ext = os.path.splitext(file.filename)[1]
    file_name = f"{file_id}{file_ext}"
    file_path = UPLOAD_DIR / file_name

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # 로그 기록
    logger.info(f"[파일 업로드] IP: {client_ip} | "
                f"파일: {file.filename} ({len(content)} bytes) | "
                f"업로드: {uploaded_by} | 요청ID: {request_id[:8]}...")

    # DB에 기록
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO attachments (
            id, request_id, file_name, file_path, file_type, file_size, uploaded_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        file_id, request_id, file.filename, str(file_path),
        file.content_type, len(content), uploaded_by, now()
    ))

    conn.commit()
    conn.close()

    file_url = f"/uploads/{file_name}"

    return {
        "id": file_id,
        "file_name": file.filename,
        "file_url": file_url,
        "file_type": file.content_type,
        "file_size": len(content)
    }

@app.get("/api/as-requests/{request_id}/messages")
async def get_chat_messages(request_id: str):
    """채팅 메시지 조회"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM chat_messages WHERE request_id = ? ORDER BY created_at ASC
    """, (request_id,))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

@app.get("/api/as-requests/{request_id}/history")
async def get_status_history(request_id: str):
    """상태 변경 히스토리 조회"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM status_history WHERE request_id = ? ORDER BY created_at ASC
    """, (request_id,))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

@app.get("/api/statistics/dashboard")
async def get_dashboard_statistics():
    """대시보드 통계"""
    conn = get_db()
    cursor = conn.cursor()

    # 전체 요청 수
    cursor.execute("SELECT COUNT(*) FROM as_requests")
    total_requests = cursor.fetchone()[0]

    # 상태별 카운트
    cursor.execute("""
        SELECT status, COUNT(*) as count FROM as_requests GROUP BY status
    """)
    status_counts = {row[0]: row[1] for row in cursor.fetchall()}

    # 평균 응답 시간 (완료된 건)
    cursor.execute("""
        SELECT AVG(
            (julianday(completed_at) - julianday(created_at)) * 24 * 60
        ) as avg_minutes
        FROM as_requests
        WHERE completed_at IS NOT NULL
    """)
    avg_response_time = cursor.fetchone()[0] or 0

    # 오늘 요청 수
    cursor.execute("""
        SELECT COUNT(*) FROM as_requests
        WHERE date(created_at) = date('now')
    """)
    today_requests = cursor.fetchone()[0]

    conn.close()

    return {
        "total_requests": total_requests,
        "status_counts": status_counts,
        "avg_response_time_minutes": round(avg_response_time, 1),
        "today_requests": today_requests
    }

# ==================== 사용자 활동 로그 API ====================

@app.post("/api/logs/activity")
async def log_user_activity(request: Request, log: UserActivityLog):
    """사용자 활동 로그 기록"""
    conn = get_db()
    cursor = conn.cursor()

    log_id = generate_id()
    ip_address = get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "")

    cursor.execute("""
        INSERT INTO user_activity_logs (
            id, ip_address, user_agent, action_type, action_detail,
            search_keyword, page_url, bay_number, request_id, session_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        log_id, ip_address, user_agent, log.action_type, log.action_detail,
        log.search_keyword, log.page_url, log.bay_number, log.request_id,
        log.session_id, now()
    ))

    conn.commit()
    conn.close()

    return {
        "id": log_id,
        "ip_address": ip_address,
        "logged_at": now()
    }

@app.get("/api/logs/activity")
async def get_activity_logs(
    ip_address: Optional[str] = None,
    action_type: Optional[str] = None,
    search_keyword: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    """사용자 활동 로그 조회"""
    conn = get_db()
    cursor = conn.cursor()

    query = "SELECT * FROM user_activity_logs WHERE 1=1"
    params = []

    if ip_address:
        query += " AND ip_address = ?"
        params.append(ip_address)

    if action_type:
        query += " AND action_type = ?"
        params.append(action_type)

    if search_keyword:
        query += " AND search_keyword LIKE ?"
        params.append(f"%{search_keyword}%")

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    rows = cursor.fetchall()

    # 전체 개수 조회
    count_query = "SELECT COUNT(*) FROM user_activity_logs WHERE 1=1"
    count_params = []

    if ip_address:
        count_query += " AND ip_address = ?"
        count_params.append(ip_address)

    if action_type:
        count_query += " AND action_type = ?"
        count_params.append(action_type)

    if search_keyword:
        count_query += " AND search_keyword LIKE ?"
        count_params.append(f"%{search_keyword}%")

    cursor.execute(count_query, count_params)
    total = cursor.fetchone()[0]

    conn.close()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "logs": [dict(row) for row in rows]
    }

@app.get("/api/logs/statistics")
async def get_log_statistics():
    """로그 통계 조회"""
    conn = get_db()
    cursor = conn.cursor()

    # 전체 로그 수
    cursor.execute("SELECT COUNT(*) FROM user_activity_logs")
    total_logs = cursor.fetchone()[0]

    # 고유 IP 수
    cursor.execute("SELECT COUNT(DISTINCT ip_address) FROM user_activity_logs")
    unique_ips = cursor.fetchone()[0]

    # 액션 타입별 카운트
    cursor.execute("""
        SELECT action_type, COUNT(*) as count
        FROM user_activity_logs
        GROUP BY action_type
        ORDER BY count DESC
    """)
    action_counts = {row[0]: row[1] for row in cursor.fetchall()}

    # 검색 키워드 TOP 10
    cursor.execute("""
        SELECT search_keyword, COUNT(*) as count
        FROM user_activity_logs
        WHERE search_keyword IS NOT NULL AND search_keyword != ''
        GROUP BY search_keyword
        ORDER BY count DESC
        LIMIT 10
    """)
    top_searches = [{"keyword": row[0], "count": row[1]} for row in cursor.fetchall()]

    # IP별 활동 TOP 10
    cursor.execute("""
        SELECT ip_address, COUNT(*) as count
        FROM user_activity_logs
        GROUP BY ip_address
        ORDER BY count DESC
        LIMIT 10
    """)
    top_ips = [{"ip": row[0], "count": row[1]} for row in cursor.fetchall()]

    # 오늘 활동 수
    cursor.execute("""
        SELECT COUNT(*) FROM user_activity_logs
        WHERE date(created_at) = date('now')
    """)
    today_activities = cursor.fetchone()[0]

    # 시간대별 활동 (최근 24시간)
    cursor.execute("""
        SELECT
            strftime('%H', created_at) as hour,
            COUNT(*) as count
        FROM user_activity_logs
        WHERE datetime(created_at) >= datetime('now', '-24 hours')
        GROUP BY hour
        ORDER BY hour
    """)
    hourly_activity = [{"hour": row[0], "count": row[1]} for row in cursor.fetchall()]

    conn.close()

    return {
        "total_logs": total_logs,
        "unique_ips": unique_ips,
        "action_counts": action_counts,
        "top_searches": top_searches,
        "top_ips": top_ips,
        "today_activities": today_activities,
        "hourly_activity": hourly_activity
    }

@app.get("/api/logs/ip/{ip_address}")
async def get_logs_by_ip(ip_address: str, limit: int = 50):
    """특정 IP 주소의 활동 로그 조회"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM user_activity_logs
        WHERE ip_address = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (ip_address, limit))

    rows = cursor.fetchall()

    # 해당 IP 통계
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(DISTINCT action_type) as unique_actions,
            MIN(created_at) as first_seen,
            MAX(created_at) as last_seen
        FROM user_activity_logs
        WHERE ip_address = ?
    """, (ip_address,))

    stats = dict(cursor.fetchone())

    conn.close()

    return {
        "ip_address": ip_address,
        "statistics": stats,
        "logs": [dict(row) for row in rows]
    }

# ==================== 출동 스케줄 API ====================

@app.post("/api/visit-schedules")
async def create_visit_schedule(schedule: VisitSchedule, http_request: Request):
    """출동 스케줄 제안 생성"""
    client_ip = get_client_ip(http_request)

    conn = get_db()
    cursor = conn.cursor()

    schedule_id = generate_id()

    # 로그 기록
    logger.info(f"[출동 스케줄 제안] IP: {client_ip} | "
                f"제안자: {schedule.proposed_by} | "
                f"일시: {schedule.proposed_date} {schedule.proposed_time} | "
                f"요청ID: {schedule.request_id[:8]}...")

    cursor.execute("""
        INSERT INTO visit_schedules (
            id, request_id, proposed_by, proposed_date, proposed_time,
            notes, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?)
    """, (
        schedule_id, schedule.request_id, schedule.proposed_by,
        schedule.proposed_date, schedule.proposed_time, schedule.notes, now()
    ))

    conn.commit()

    cursor.execute("SELECT * FROM visit_schedules WHERE id = ?", (schedule_id,))
    row = cursor.fetchone()
    conn.close()

    result = dict(row)

    # WebSocket으로 알림
    await manager.send_message({
        "type": "schedule_proposed",
        "data": result
    }, schedule.request_id)

    return result

@app.get("/api/visit-schedules/{request_id}")
async def get_visit_schedules(request_id: str):
    """특정 AS 요청의 스케줄 조회"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM visit_schedules
        WHERE request_id = ?
        ORDER BY created_at DESC
    """, (request_id,))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]

@app.patch("/api/visit-schedules/{schedule_id}")
async def update_visit_schedule(schedule_id: str, update: VisitScheduleUpdate):
    """스케줄 수정/수락/거부"""
    conn = get_db()
    cursor = conn.cursor()

    # 현재 스케줄 조회
    cursor.execute("SELECT * FROM visit_schedules WHERE id = ?", (schedule_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="스케줄을 찾을 수 없습니다")

    current_schedule = dict(row)

    # 업데이트할 필드 준비
    updates = []
    values = []

    if update.status:
        updates.append("status = ?")
        values.append(update.status)

    if update.proposed_date:
        updates.append("proposed_date = ?")
        values.append(update.proposed_date)

    if update.proposed_time:
        updates.append("proposed_time = ?")
        values.append(update.proposed_time)

    if update.notes:
        updates.append("notes = ?")
        values.append(update.notes)

    updates.append("updated_at = ?")
    values.append(now())

    if updates:
        values.append(schedule_id)
        cursor.execute(f"""
            UPDATE visit_schedules SET {', '.join(updates)} WHERE id = ?
        """, values)

        conn.commit()

    # 업데이트된 스케줄 조회
    cursor.execute("SELECT * FROM visit_schedules WHERE id = ?", (schedule_id,))
    row = cursor.fetchone()
    conn.close()

    result = dict(row)

    # WebSocket으로 알림
    await manager.send_message({
        "type": "schedule_updated",
        "data": result
    }, current_schedule['request_id'])

    return result

# ==================== WebSocket 엔드포인트 ====================

@app.websocket("/ws/{request_id}")
async def websocket_endpoint(websocket: WebSocket, request_id: str):
    """WebSocket 연결 - 채팅방별"""
    await manager.connect(websocket, request_id)

    try:
        while True:
            data = await websocket.receive_json()

            # 받은 메시지를 같은 채팅방의 모든 연결에 브로드캐스트
            await manager.send_message(data, request_id)

    except WebSocketDisconnect:
        manager.disconnect(websocket, request_id)

@app.websocket("/ws/technician/global")
async def technician_websocket(websocket: WebSocket):
    """기사 대시보드 전용 WebSocket - 실시간 알림"""
    await manager.connect_technician(websocket)

    try:
        while True:
            # 연결 유지를 위한 ping/pong
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect_technician(websocket)

# ==================== 로그 조회 API ====================

@app.get("/api/logs/realtime")
async def get_realtime_logs(lines: int = 100):
    """실시간 로그 조회 (최근 N개 라인)"""
    log_file = LOG_DIR / "as_system.log"

    if not log_file.exists():
        return {"logs": [], "total": 0}

    try:
        # 파일 끝에서부터 N개 라인 읽기
        with open(log_file, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

        return {
            "logs": [line.strip() for line in recent_lines],
            "total": len(all_lines)
        }
    except Exception as e:
        logger.error(f"로그 파일 읽기 실패: {e}")
        return {"logs": [], "total": 0, "error": str(e)}

@app.get("/api/logs/search")
async def search_logs(keyword: str, lines: int = 100):
    """로그 검색"""
    log_file = LOG_DIR / "as_system.log"

    if not log_file.exists():
        return {"logs": [], "total": 0}

    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()

        # 키워드로 필터링
        filtered = [line.strip() for line in all_lines if keyword.lower() in line.lower()]
        recent = filtered[-lines:] if len(filtered) > lines else filtered

        return {
            "logs": recent,
            "total": len(filtered),
            "keyword": keyword
        }
    except Exception as e:
        logger.error(f"로그 검색 실패: {e}")
        return {"logs": [], "total": 0, "error": str(e)}

# ==================== 정적 파일 서빙 ====================

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# ==================== 서버 실행 ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=53001)

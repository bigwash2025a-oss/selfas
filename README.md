# SelfAS - 셀프 세차장 AS 관리 시스템

셀프 세차장을 위한 실시간 AS(After Service) 관리 시스템입니다.

## 주요 기능

### 고객 기능
- 🚗 실시간 AS 요청 접수
- 💬 기사와 1:1 채팅
- 📅 출동 스케줄 수락/변경/거절
- 📎 사진 첨부 기능
- 🔔 실시간 알림

### 기사 기능
- 📋 AS 요청 대시보드
- 🗓️ 출동 스케줄 관리
- 💬 고객과 실시간 채팅
- 📊 로그 분석 및 모니터링
- 🚨 긴급 출동 알림

### 시스템 기능
- 📝 자동 로그 기록 (IP, 고객정보, 활동내역)
- 🔄 실시간 WebSocket 통신
- 🎯 베이별 장비 관리
- 📈 활동 통계 및 분석

## 기술 스택

- **Backend**: Python FastAPI + WebSocket
- **Frontend**: HTML5 + Vanilla JavaScript
- **Database**: SQLite
- **Logging**: RotatingFileHandler (10MB, 5 backups)

## 설치 및 실행

```bash
# 백엔드 서버 실행
cd backend
python3 as_system.py

# 브라우저에서 접속
# 고객용: http://localhost:53001/customer-as-request.html
# 기사용: http://localhost:53001/technician-app.html
```

## 파일 구조

```
selfas/
├── backend/
│   └── as_system.py          # FastAPI 백엔드 서버
├── logs/
│   └── as_system.log         # 시스템 로그
├── customer-as-request.html  # 고객 AS 요청 폼
├── customer-chat.html        # 고객 채팅 인터페이스
├── chat-widget.html          # 채팅 위젯
├── technician-app.html       # 기사 대시보드
├── as-request-detail.html    # AS 요청 상세보기
└── staff-dashboard.html      # 스태프 대시보드
```

## 로그 기록

시스템은 다음 활동을 자동으로 로그에 기록합니다:
- AS 요청 생성 (고객 정보, IP, 베이, 장비, 문제)
- 채팅 메시지 (발신자, 수신자, 내용)
- 파일 업로드 (파일명, 크기)
- 스케줄 변경 (날짜, 시간, 변경사유)

## 개발 이력

- 2025-11-23: 로그 시스템 및 고객 스케줄 상호작용 기능 추가
- 실시간 로그 뷰어 구현
- 차량 정보 필드 제거
- AS 요청 상세보기 페이지 구현

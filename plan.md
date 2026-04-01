You are a senior backend engineer. 

  

Create a production-ready backend server project using Python FastAPI that integrates with Naver Search API to analyze parking congestion around baseball stadiums. 

  

Requirements 

1. Basic Setup 

Use FastAPI 

Use Python 3.10+ 

Project structure should be clean and modular 

Include requirements.txt 

2. Naver API Integration 

Use Naver Search API (Blog or News search) 

Read API credentials from environment variables: 

NAVER_CLIENT_ID 

NAVER_CLIENT_SECRET 

Create a service module to call Naver API 

3. Core Feature: Parking Congestion Analysis 

Endpoint: GET /parking-status?location=잠실 

Fetch search results using keywords: 

"{location} 주차장 혼잡" 

"{location} 주차 만차" 

"{location} 주차 가능" 

Analyze text data using keyword scoring 

4. Keyword Scoring Logic 

Negative keywords (increase congestion score): 

["만차", "혼잡", "막힘", "주차불가"] 

Positive keywords (decrease score): 

["여유", "널널", "자리있음"] 

Return: 

congestion level: "good" | "normal" | "bad" 

score (int) 

confidence (0~1 based on data volume) 

5. Caching 

Implement in-memory cache (TTL: 5 minutes) 

Avoid calling Naver API for identical requests within cache time 

6. Scheduler (Optional but preferred) 

Periodically pre-fetch popular locations (e.g., 잠실, 고척) 

Use background tasks or APScheduler 

7. API Response Example 

  

{ 

"location": "잠실", 

"status": "bad", 

"score": 12, 

"confidence": 0.78, 

"message": "현재 주차 혼잡" 

} 

  

8. Additional Requirements 

Proper error handling 

Logging 

Clean code with comments 

Separate files: 

main.py 

services/naver_api.py 

services/analyzer.py 

utils/cache.py 

9. Run Instructions 

Include how to run the server locally 

  

Do not overcomplicate the system. Keep it clean, scalable, and readable. 

  

🔥 왜 이 프롬프트가 좋은지 (이건 알고 써라) 

  

이건 그냥 요청이 아니라 

  

“아키텍처 + 기능 + 코드 구조”까지 강제한 프롬프트 

  

다. 

  

Claude가 멍청하게 이런 거 안 만들게 막는 장치: 

  

파일 분리 강제 

캐싱 강제 

환경변수 강제 

로직 명확화 

💡 추가로 넣으면 더 좋아지는 옵션 

  

원하면 이거 뒤에 붙여라 

  

Add Docker support (Dockerfile) 

Add .env example file 

Add simple unit tests 
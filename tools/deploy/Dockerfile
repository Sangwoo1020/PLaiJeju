# 공개 인터넷 데모 서버용 이미지. 센터 PC 실행(run.bat)과는 상관없다.
# 빌드: 프로젝트 맨 위에서  docker build -f tools/deploy/Dockerfile -t plaijeju .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib GOLMOK_PUBLIC_DEMO=1
RUN apt-get update && apt-get install -y --no-install-recommends fonts-nanum && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
EXPOSE 8765
# API 키는 이미지에 넣지 않고, 실행할 때 --env-file 로 넘긴다
CMD ["python", "-m", "uvicorn", "golmok.app:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8765", "--proxy-headers", "--forwarded-allow-ips", "*"]

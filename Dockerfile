FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY study_bot ./study_bot
ENV DB_PATH=/data/study_bot.db
VOLUME ["/data"]
CMD ["python", "-m", "study_bot"]

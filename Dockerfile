FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt && useradd --uid 10001 --create-home planner && mkdir /data && chown planner:planner /data
COPY --chown=planner:planner server.py backup.py migrations.py ./
COPY --chown=planner:planner dist ./dist
USER planner
EXPOSE 8080
CMD ["python", "server.py"]

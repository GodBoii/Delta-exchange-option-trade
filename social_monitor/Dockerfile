FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TWS_TELEMETRY=0
WORKDIR /monitor
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --uid 10001 --user-group --create-home monitor \
    && mkdir /monitor/state && chown monitor:monitor /monitor/state
COPY config.example.toml ./config.toml
USER monitor
ENTRYPOINT ["python", "-m", "social_monitor", "--config", "/monitor/config.toml"]
CMD ["run"]

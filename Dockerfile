FROM python:3.11-slim

WORKDIR /app
COPY planner ./planner

ENV PLANNER_DATA_DIR=/app/data \
    PLANNER_HOST=0.0.0.0

RUN python3 -m planner.cli init

EXPOSE 8000
CMD ["python3", "-m", "planner.cli", "web"]
